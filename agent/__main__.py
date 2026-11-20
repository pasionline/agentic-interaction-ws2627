import sys
import os
import json
import time
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI
from agent.tool_caller import TOOLS_SCHEMA, execute_tool

# 1. Load configuration (environment variables take precedence over .env)
load_dotenv(override=False)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL = os.getenv("MODEL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_BASE_URL = os.getenv("DATA_BASE_URL")
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))

if not all([OPENAI_BASE_URL, MODEL, OPENAI_API_KEY, DATA_BASE_URL]):
    missing = [
        k for k, v in {
            "OPENAI_BASE_URL": OPENAI_BASE_URL,
            "MODEL": MODEL,
            "OPENAI_API_KEY": OPENAI_API_KEY,
            "DATA_BASE_URL": DATA_BASE_URL,
        }.items() if not v
    ]
    print(f"Error: Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)

def get_system_prompt() -> str:
    today = datetime.now().strftime("%A, %Y-%m-%d")
    return (
        f"Today's date is {today}. Always calculate relative dates ('today', 'tomorrow', 'yesterday') strictly from {today}. "
        "You are a precise assistant with tools for calendar, weather, train connections, and user context. "
        "ONLY call tools that are strictly necessary to answer what the user explicitly asked for—never fetch weather or calendar data unless requested. "
        "If the user asks for train connections without naming a departure city, call get_user_context first to get their home city. "
        "Use lowercase ASCII slugs for German cities (e.g. 'ulm', 'muenchen') and YYYY-MM-DD for dates. "
        "CRITICAL: Never invent or guess data. If a tool returns no data (404), state clearly that no data is available. "
        "Stop calling tools as soon as you have the requested facts and answer concisely in the user's language."
    )

# 2. JSONL Logging
def log_event(
        run_id: str,
        event: str,
        duration_ms: float,
        ok: bool = True,
        tool: str | None = None,
        args: dict | None = None,
):
    """Appends a structured JSONL event to logs/run_<run_id>.jsonl."""
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
        "duration_ms": round(duration_ms, 2),
    }

    with open(log_filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# 3. Agent Loop
def main():
    if len(sys.argv) < 2:
        print('Usage: python -m agent "<question>"', file=sys.stderr)
        sys.exit(1)

    prompt = sys.argv[1]
    run_id = str(uuid.uuid4())

    log_event(
        run_id=run_id,
        event="user_task",
        duration_ms=0.0,
        ok=True,
        args={"prompt": prompt},
    )

    client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)

    messages = [
        {"role": "system", "content": get_system_prompt()},
        {"role": "user", "content": prompt},
    ]

    step = 0

    try:
        while step < MAX_STEPS:
            step += 1

            t0_llm = time.perf_counter()
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
            )
            llm_duration = (time.perf_counter() - t0_llm) * 1000
            response_message = response.choices[0].message

            # Case A: Model requested one or more tool calls
            if response_message.tool_calls:
                # Append clean assistant message with tool_calls to conversation history
                assistant_msg = {
                    "role": "assistant",
                    "content": response_message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in response_message.tool_calls
                    ],
                }
                messages.append(assistant_msg)

                for tool_call in response_message.tool_calls:
                    fn_name = tool_call.function.name
                    raw_args = tool_call.function.arguments

                    # Parse arguments safely without aborting the run on malformed JSON
                    try:
                        fn_args = json.loads(raw_args) if raw_args else {}
                        args_valid = True
                    except json.JSONDecodeError as e:
                        fn_args = {"raw_arguments": raw_args}
                        args_valid = False
                        parse_error = f"Invalid JSON arguments: {str(e)}"

                    log_event(
                        run_id=run_id,
                        event="tool_call",
                        duration_ms=llm_duration,
                        ok=args_valid,
                        tool=fn_name,
                        args=fn_args,
                    )

                    t0_tool = time.perf_counter()
                    if not args_valid:
                        tool_output = json.dumps({"error": parse_error})
                        tool_ok = False
                    else:
                        tool_output, tool_ok = execute_tool(fn_name, fn_args, DATA_BASE_URL)

                    tool_duration = (time.perf_counter() - t0_tool) * 1000

                    try:
                        parsed_result = json.loads(tool_output)
                    except Exception:
                        parsed_result = tool_output

                    log_event(
                        run_id=run_id,
                        event="tool_result",
                        duration_ms=tool_duration,
                        ok=tool_ok,
                        tool=fn_name,
                        args={"input": fn_args, "output": parsed_result},
                    )

                    # Feed tool output (even if ok=False) back to the model so it can recover
                    messages.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": fn_name,
                        "content": tool_output,
                    })

            # Case B: No tool calls -> Model decided it is done
            else:
                final_answer = response_message.content or ""
                log_event(
                    run_id=run_id,
                    event="final",
                    duration_ms=llm_duration,
                    ok=True,
                    args={"answer": final_answer, "steps": step},
                )
                print(final_answer)
                sys.exit(0)

        # If loop exits because step == MAX_STEPS, end as an ERROR
        err_msg = f"Agent exceeded maximum step limit ({MAX_STEPS}) without finishing."
        log_event(
            run_id=run_id,
            event="error",
            duration_ms=0.0,
            ok=False,
            args={"error": err_msg, "steps": step},
        )
        print(f"Error: {err_msg}", file=sys.stderr)
        sys.exit(1)

    except Exception as fatal_err:
        log_event(
            run_id=run_id,
            event="error",
            duration_ms=0.0,
            ok=False,
            args={"error": str(fatal_err), "steps": step},
        )
        print(f"Error: {fatal_err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
