# program version 0.3
#
# Changes since 0.2:
#  - Allowed values (e.g. categories such as "Mitglied") may stay in the concealed record
#  - Phase 2 is retried automatically up to MAX_ATTEMPTS times on failure
#  - No API key prefixes printed to the console anymore, unused providers removed
#  - UI: Phase 2 output is clearly labelled as the only data passed on

import os
import json
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI, APIConnectionError, APIStatusError, AuthenticationError
import gradio as gr

from privacy_check import findLeakedValues

# Architecture

# Phase 0. Load the API keys for the LOCAL LLM and the frontier LLM
# Phase 1. Real data is entered or read in
# Phase 2. The LOCAL LLM (e.g. Ollama) receives this data and changes the content (but not the structure or format)
#          A fixed rule then checks whether any original values remain. If so: retry or abort.
# Phase 3. The new record is sent to the frontier LLM and serves as a template for generating new records
# Phase 4. The frontier LLM generates the new records and returns them as the result.

# Rationale: real data is used as the basis for dummy test data, but no real data is ever sent
# to the frontier LLM. The goal is compliance with data protection requirements.

# Phase 0: initialization

load_dotenv(override=True)

OLLAMA_BASE_URL = "http://localhost:11434/v1"
openai_api_key = os.getenv("OPENAI_API_KEY")

# Print only "set / not set" – never parts of the key (important when sharing your screen)
print(f"OpenAI API Key: {'set' if openai_api_key else 'NOT set (only Local generator available)'}")

MODEL_GPT = "gpt-4.1-mini"
MODEL_LLAMA = "llama3.2"
MAX_ATTEMPTS = 3   # attempts for Phase 2 before aborting

# Local (Phase 2) – Ollama needs no real key, any value works
ollama_client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")

# Frontier (Phase 3+4) – no key, no client; the UI reports this instead of crashing
openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None


# Phase 2: conceal the original data locally

original_sampleData = "(Müller, Thomas, Breslauer Str. 13, 56269, Dierdorf, Mitglied)"
original_sampleAllowed = "Mitglied"

original_mySystemPrompt = (
    "You are a data generator. You receive one example record and create "
    "a fictitious record with the same structure but different values."
)
original_myDataFormatting = "Return the result as a JSON object."

def changeOriginalData(client, model, mySystemPrompt, myDataFormatting, mysampleData, allowedValues=""):
    mySystemPrompt += (
        " Create only ONE row of data! Do NOT add any new fields or data types "
        "other than those already given."
    )
    allowedHint = (
        f"These category values may be kept unchanged: {allowedValues}.\n" if allowedValues.strip() else ""
    )
    myUserPrompt = (
        f"Example record:\n{mysampleData}\n\n"
        "Generate a dummy record with the same fields, but NONE of the original values.\n"
        f"{allowedHint}"
        f"{myDataFormatting}"
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": mySystemPrompt},
            {"role": "user", "content": myUserPrompt},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content


# Privacy check between Phase 2 and Phase 3 → see privacy_check.py


# Phase 3 + Phase 4: use the concealed record as a template for new records

frontier_mySystemPrompt = (
    "You are a test data generator. You receive one example record and generate "
    "multiple fictitious records with exactly the same fields and data types. "
    "Every record must have different, realistic values. Never reuse values from "
    "the example. Do NOT add any new fields."
)

def createDataUsingFrontierLLM(client, model, mySystemPrompt, sampleData, intRowAmount):
    myUserPrompt = (
        f"Example record:\n{sampleData}\n\n"
        f"Generate exactly {intRowAmount} new records.\n"
        'Return a JSON object of the form {"records": [ ... ]} where the array '
        f"contains exactly {intRowAmount} records, each with the same fields as the example."
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": mySystemPrompt},
            {"role": "user", "content": myUserPrompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.9,   # a bit more variation between records
    )
    return response.choices[0].message.content


# Web UI (Gradio)

OPTION_LOCAL = "Local"
OPTION_FRONTIER = "Frontier (online)"

def _parseJson(raw, step):
    # Returns (data, error message)
    try:
        return json.loads(raw), None
    except (json.JSONDecodeError, TypeError):
        return None, f"❌ {step}: the model did not return valid JSON."

def _apiErrorMessage(error, step):
    if isinstance(error, APIConnectionError):
        if step.startswith("Phase 2") or OLLAMA_BASE_URL in str(getattr(error, "request", "")):
            return f"❌ {step}: Ollama is not reachable at {OLLAMA_BASE_URL}. Is 'ollama serve' running?"
        return f"❌ {step}: the server is not reachable. Check your internet connection."
    if isinstance(error, AuthenticationError):
        return f"❌ {step}: the API key was rejected. Check OPENAI_API_KEY in your .env file."
    if isinstance(error, APIStatusError) and error.status_code == 404:
        return f"❌ {step}: model not found. For Ollama run 'ollama pull {MODEL_LLAMA}'."
    return f"❌ {step}: {type(error).__name__}: {error}"

def runPipeline(originalData, allowedValues, generatorChoice, rowCount):
    # Outputs: intermediate result, table, raw JSON, status
    if not originalData or not originalData.strip():
        yield "", None, "", "❌ Please enter an original record."
        return
    rowCount = int(rowCount or 0)
    if not 1 <= rowCount <= 50:
        yield "", None, "", "❌ Result Lines Count must be between 1 and 50."
        return
    allowedValues = allowedValues or ""

    # Check the generator before Phase 2 so no work is wasted
    if generatorChoice == OPTION_FRONTIER:
        if openai_client is None:
            yield "", None, "", "❌ No OpenAI API key found. Set OPENAI_API_KEY in your .env file or choose 'Local' as Data Generator."
            return
        generatorClient, generatorModel = openai_client, MODEL_GPT
    else:
        generatorClient, generatorModel = ollama_client, MODEL_LLAMA

    # Phase 2 – always local, with automatic retries
    anonymizedData = None
    lastShown = ""
    lastError = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        yield lastShown, None, "", f"⏳ Phase 2: concealing data locally (attempt {attempt}/{MAX_ATTEMPTS})… {lastError}"
        try:
            anonymizedRaw = changeOriginalData(
                ollama_client, MODEL_LLAMA, original_mySystemPrompt, original_myDataFormatting,
                originalData, allowedValues,
            )
        except Exception as error:
            # Connection error: retrying will not help
            yield lastShown, None, "", _apiErrorMessage(error, "Phase 2 (Data Concealer)")
            return

        candidate, error = _parseJson(anonymizedRaw, "Phase 2 (Data Concealer)")
        if error:
            lastShown, lastError = anonymizedRaw or "", "(previous attempt: invalid JSON)"
            continue

        lastShown = json.dumps(candidate, indent=2, ensure_ascii=False)
        # Privacy check: nothing is passed on if original values slip through
        leaked = findLeakedValues(originalData, candidate, allowedValues)
        if leaked:
            lastError = "🛑 (previous attempt blocked – original values found: " + ", ".join(leaked) + ")"
            continue

        anonymizedData = candidate
        break

    if anonymizedData is None:
        yield lastShown, None, "", (
            f"🛑 Stopped after {MAX_ATTEMPTS} attempts. {lastError.replace('🛑 ', '')} "
            "Nothing was sent to the generator."
        )
        return

    anonymizedPretty = json.dumps(anonymizedData, indent=2, ensure_ascii=False)
    attemptInfo = f" (privacy check passed on attempt {attempt})"

    # Phase 3 + 4
    yield anonymizedPretty, None, "", f"⏳ Phase 3+4: generating {rowCount} records ({generatorChoice})…{attemptInfo}"
    try:
        resultRaw = createDataUsingFrontierLLM(
            generatorClient, generatorModel, frontier_mySystemPrompt, anonymizedPretty, rowCount
        )
    except Exception as error:
        yield anonymizedPretty, None, "", _apiErrorMessage(error, "Phase 3+4 (Data Generator)")
        return

    resultData, error = _parseJson(resultRaw, "Phase 3+4 (Data Generator)")
    if error:
        yield anonymizedPretty, None, resultRaw or "", error
        return

    records = resultData.get("records") if isinstance(resultData, dict) else None
    if not isinstance(records, list):
        yield anonymizedPretty, None, resultRaw, "❌ Phase 3+4: the JSON has no 'records' list."
        return

    table = pd.json_normalize(records) if records and all(isinstance(r, dict) for r in records) else None
    resultPretty = json.dumps(resultData, indent=2, ensure_ascii=False)
    if len(records) != rowCount:
        status = f"⚠️ Received {len(records)} records instead of {rowCount}.{attemptInfo}"
    else:
        status = f"✅ Done – {len(records)} records generated.{attemptInfo}"
    yield anonymizedPretty, table, resultPretty, status


with gr.Blocks(title="Test Data Generator") as demo:
    gr.Markdown("# Test Data Generator")
    with gr.Tabs():
        with gr.Tab("Configuration"):
            gr.Radio(
                [OPTION_LOCAL, OPTION_FRONTIER], value=OPTION_LOCAL,
                label="Data Concealer", interactive=False,
            )
            gr.Markdown("*Our purpose: real data stays on your computer.*")
            generatorChoice = gr.Radio(
                [OPTION_LOCAL, OPTION_FRONTIER], value=OPTION_FRONTIER,
                label="Data Generator",
            )

        with gr.Tab("Generate"):
            originalInput = gr.Textbox(
                label="Original Data", lines=4, placeholder=original_sampleData,
            )
            allowedInput = gr.Textbox(
                label="Values allowed to stay (categories, comma separated)",
                placeholder=original_sampleAllowed,
            )
            rowCountInput = gr.Number(
                label="Result Lines Count", value=5, minimum=1, maximum=50, precision=0,
            )
            createButton = gr.Button("CREATE", variant="primary")
            statusOutput = gr.Textbox(label="Status", interactive=False)
            anonymizedOutput = gr.Code(
                label="Concealed record – the ONLY data passed to the generator", language="json",
            )
            tableOutput = gr.Dataframe(label="Result", interactive=False)
            resultJsonOutput = gr.Code(label="Result (raw JSON)", language="json")

    createButton.click(
        runPipeline,
        inputs=[originalInput, allowedInput, generatorChoice, rowCountInput],
        outputs=[anonymizedOutput, tableOutput, resultJsonOutput, statusOutput],
    )

if __name__ == "__main__":
    demo.launch()
