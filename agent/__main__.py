import sys
import os
import json
import time
import uuid
from datetime import datetime, timezone
import requests
from dotenv import load_dotenv
from openai import OpenAI

# 1. Environment-Variablen laden
load_dotenv(override=False)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL = os.getenv("MODEL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_BASE_URL = os.getenv("DATA_BASE_URL")

# Abbruch bei fehlender Konfiguration
if not all([OPENAI_BASE_URL, MODEL, OPENAI_API_KEY, DATA_BASE_URL]):
    missing = [k for k, v in {
        "OPENAI_BASE_URL": OPENAI_BASE_URL,
        "MODEL": MODEL,
        "OPENAI_API_KEY": OPENAI_API_KEY,
        "DATA_BASE_URL": DATA_BASE_URL
    }.items() if not v]
    print(f"Error: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)


# 2. Spezifikationskonformes Logging
def log_event(
        run_id: str,
        event: str,
        duration_ms: float,
        ok: bool = True,
        tool: str | None = None,
        args: dict | None = None
):
    """
    Schreibt ein strukturiertes Event gemäß Spezifikation in logs/run_<run_id>.jsonl
    Felder: ts, run_id, event, tool, args, ok, model, duration_ms
    """
    os.makedirs("logs", exist_ok=True)
    log_filename = os.path.join("logs", f"run_{run_id}.jsonl")

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "event": event,
        "tool": tool,
        "args": args if args is not None else {},
        "ok": ok,
        "model": MODEL,
        "duration_ms": round(duration_ms, 2)
    }

    with open(log_filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# 3. Das Tool: Liest Kalendertage vom Mock-Server
def get_calendar_events(date: str) -> tuple[str, bool, float]:
    """
    Ruft Kalenderdaten unter {DATA_BASE_URL}/calendar/{date}.json ab.
    Bei HTTP 404 wird kein Fehler geworfen, sondern ein leeres Ergebnis gemeldet.
    Rückgabe: (response_text, ok_status, duration_ms)
    """
    t0 = time.perf_counter()
    url = f"{DATA_BASE_URL.rstrip('/')}/calendar/{date}.json"

    try:
        response = requests.get(url, timeout=10)
        duration_ms = (time.perf_counter() - t0) * 1000

        if response.status_code == 200:
            return response.text, True, duration_ms
        elif response.status_code == 404:
            # HTTP 404 bedeutet laut Spezifikation: Keine Daten für diesen Tag
            no_data_payload = json.dumps({
                "events": [],
                "message": f"No appointments found for date {date}."
            })
            return no_data_payload, True, duration_ms
        else:
            err_msg = json.dumps({"error": f"HTTP {response.status_code} while fetching calendar data"})
            return err_msg, False, duration_ms

    except Exception as e:
        duration_ms = (time.perf_counter() - t0) * 1000
        return json.dumps({"error": str(e)}), False, duration_ms


# Definition des Tool-Schemas für die OpenAI API
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Fetch calendar appointments for a specific day given in YYYY-MM-DD format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The target date in YYYY-MM-DD format (e.g., '2026-10-29').",
                    }
                },
                "required": ["date"],
            },
        },
    }
]


# 4. Agent Lifecycle / Main Loop
def main():
    if len(sys.argv) < 2:
        print("Usage: python -m agent \"<question>\"", file=sys.stderr)
        sys.exit(1)

    prompt = sys.argv[1]
    run_id = str(uuid.uuid4())

    # Event 1: user_task
    log_event(
        run_id=run_id,
        event="user_task",
        duration_ms=0.0,
        ok=True,
        tool=None,
        args={"prompt": prompt}
    )

    client = OpenAI(
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
    )

    messages = [{"role": "user", "content": prompt}]

    try:
        # Erster Modellaufruf (Model -> Tool)
        t0_llm1 = time.perf_counter()
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        llm1_duration = (time.perf_counter() - t0_llm1) * 1000
        response_message = response.choices[0].message

        # Wenn das Modell die Benutzung des Tools entscheidet
        if response_message.tool_calls:
            messages.append(response_message)

            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                # Event 2: tool_call
                log_event(
                    run_id=run_id,
                    event="tool_call",
                    duration_ms=llm1_duration,
                    ok=True,
                    tool=function_name,
                    args=function_args
                )

                if function_name == "get_calendar_events":
                    date_arg = function_args.get("date")
                    tool_output, ok_status, tool_duration = get_calendar_events(date=date_arg)

                    # Event 3: tool_result
                    try:
                        parsed_output = json.loads(tool_output)
                    except Exception:
                        parsed_output = tool_output

                    log_event(
                        run_id=run_id,
                        event="tool_result",
                        duration_ms=tool_duration,
                        ok=ok_status,
                        tool=function_name,
                        args={"date": date_arg, "result": parsed_output}
                    )

                    # Antwort des Tools als Rolle 'tool' an den Verlauf anfügen
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": tool_output,
                    })

            # Zweiter Modellaufruf (Tool -> Model) zur Generierung der Antwort
            t0_llm2 = time.perf_counter()
            second_response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
            )
            llm2_duration = (time.perf_counter() - t0_llm2) * 1000
            final_answer = second_response.choices[0].message.content
        else:
            final_answer = response_message.content
            llm2_duration = llm1_duration

        # Event 4: final
        log_event(
            run_id=run_id,
            event="final",
            duration_ms=llm2_duration,
            ok=True,
            tool=None,
            args={"answer": final_answer}
        )

        # Ausgabe auf stdout & erfolgreicher Exit Code 0
        print(final_answer)
        sys.exit(0)

    except Exception as err:
        # Event 5: error (bei unerwarteten Ausnahmen)
        log_event(
            run_id=run_id,
            event="error",
            duration_ms=0.0,
            ok=False,
            tool=None,
            args={"error": str(err)}
        )
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()