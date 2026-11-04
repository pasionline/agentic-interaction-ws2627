import sys
import os
import json
import time
import uuid
import datetime
import requests
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env if present
load_dotenv()

# Read environment variables
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL = os.getenv("MODEL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_BASE_URL = os.getenv("DATA_BASE_URL")

# Fail fast if required configuration is missing
if not OPENAI_API_KEY:
    print("Error: OPENAI_API_KEY is not set in the environment or .env file.", file=sys.stderr)
    sys.exit(1)

if not OPENAI_BASE_URL or not MODEL or not DATA_BASE_URL:
    print("Error: Missing required environment configuration (OPENAI_BASE_URL, MODEL, or DATA_BASE_URL).", file=sys.stderr)
    sys.exit(1)


# --- Tool Definition ---

def get_calendar_events(date: str) -> str:
    """
    Fetches calendar appointments for a specific date (YYYY-MM-DD) from the remote mock server.
    """
    url = f"{DATA_BASE_URL.rstrip('/')}/calendar/{date}.json"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.text
        elif response.status_code == 404:
            return json.dumps({"events": [], "message": f"No events found for {date}."})
        else:
            return json.dumps({"error": f"Failed to fetch data (HTTP {response.status_code})"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# OpenAI Tool schema specification
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Fetch all calendar events/appointments for a specific day given in YYYY-MM-DD format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The date to query in YYYY-MM-DD format (e.g. '2026-10-29').",
                    }
                },
                "required": ["date"],
            },
        },
    }
]


# --- Logging Function ---
# TODO fix logs (specification in exercise 1 slides)
def write_log(run_id: str, prompt: str, tool_calls: list, final_response: str):
    """
    Writes execution details into a JSONL log file under the logs/ directory.
    """
    os.makedirs("logs", exist_ok=True)
    log_filename = os.path.join("logs", f"run_{run_id}.jsonl")

    log_entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_id": run_id,
        "prompt": prompt,
        "tool_calls": tool_calls,
        "final_response": final_response,
    }

    with open(log_filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# --- Execution Pipeline ---

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m agent \"<question>\"", file=sys.stderr)
        sys.exit(1)

    prompt = sys.argv[1]
    run_id = str(uuid.uuid4()) # uuid for logging

    client = OpenAI(
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
    )

    messages = [
        {"role": "user", "content": prompt}
    ]

    logged_tool_calls = []

    # Step 1: Initial call to model
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )

    response_message = response.choices[0].message

    # Step 2: Check if tool execution was requested
    if response_message.tool_calls:
        messages.append(response_message)  # Extend conversation with model's tool call request

        for tool_call in response_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)

            if function_name == "get_calendar_events":
                tool_output = get_calendar_events(date=function_args.get("date"))

                logged_tool_calls.append({
                    "id": tool_call.id,
                    "name": function_name,
                    "args": function_args,
                    "output": tool_output
                })

                messages.append({
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": tool_output,
                })

        # Step 3: Second call to model to process tool output into final response
        second_response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        final_answer = second_response.choices[0].message.content
    else:
        final_answer = response_message.content

    # Output answer to standard output
    print(final_answer)

    # Log the execution
    write_log(run_id, prompt, logged_tool_calls, final_answer)

    sys.exit(0)


if __name__ == "__main__":
    main()
