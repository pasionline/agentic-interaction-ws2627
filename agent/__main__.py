import sys
import os
import json
import time
import uuid
from datetime import datetime, timezone
from dotenv import load_dotenv
from openai import OpenAI
from agent.tool_caller import TOOLS_SCHEMA, IRREVERSIBLE_TOOLS, execute_tool

# 1. Konfiguration laden (Umgebungsvariablen haben Vorrang vor .env)
load_dotenv(override=False)

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
MODEL = os.getenv("MODEL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATA_BASE_URL = os.getenv("DATA_BASE_URL")
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))
CONDITION = os.getenv("CONDITION")
AUTO_APPROVE = os.getenv("AUTO_APPROVE") == "1"

# Validierung der Pflicht-Umgebungsvariablen
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

# Task 03: CONDITION muss 'preview' oder 'direct' sein
if CONDITION not in ("preview", "direct"):
    print(
        f"Error: CONDITION must be set to 'preview' or 'direct' (got: {CONDITION!r}).",
        file=sys.stderr,
    )
    sys.exit(1)


def get_system_prompt() -> str:
    """Identischer System-Prompt für beide Bedingungen (CONDITION wird dem Modell nie mitgeteilt)."""
    today = datetime.now().strftime("%A, %Y-%m-%d")
    return (
        f"Today's date is {today}. Always calculate relative dates ('today', 'tomorrow', 'yesterday') strictly from {today}. "
        "You are a precise assistant with tools for reading and creating calendar appointments, checking weather, train connections, and user context. "
        "ONLY call tools that are strictly necessary to answer what the user explicitly asked for. "
        "If the user asks for train connections without naming a departure city, call get_user_context first to get their home city. "
        "Use lowercase ASCII slugs for German cities (e.g. 'ulm', 'muenchen'), YYYY-MM-DD for dates, and HH:MM for times. "
        "CRITICAL: Never invent or guess data. If a tool returns no data (404), state clearly that no data is available. "
        "If an action is denied by the user, acknowledge it and finish your response without retrying the denied action. "
        "Stop calling tools as soon as you have completed the request and answer concisely in the user's language."
    )


# 2. JSONL-Logging (Task 05: inkl. Feld 'condition')
def log_event(
        run_id: str,
        event: str,
        duration_ms: float,
        ok: bool = True,
        tool: str | None = None,
        args: dict | None = None,
):
    """Schreibt ein strukturiertes Event als JSON-Zeile in logs/run_<run_id>.jsonl."""
    os.makedirs("logs", exist_ok=True)
    log_filename = os.path.join("logs", f"run_{run_id}.jsonl")

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "condition": CONDITION,
        "event": event,
        "tool": tool,
        "args": args if args is not None else {},
        "ok": ok,
        "model": MODEL,
        "duration_ms": round(duration_ms, 2),
    }

    with open(log_filename, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# 3. Approval-Dialog (nur für CONDITION == 'preview' & IRREVERSIBLE_TOOLS)
def request_approval(run_id: str, fn_name: str, fn_args: dict) -> bool:
    """
    Zeigt Plan & Rückfrage auf stderr an, loggt approval_request und
    approval_granted / approval_denied.
    """
    log_event(
        run_id=run_id,
        event="approval_request",
        duration_ms=0.0,
        ok=True,
        tool=fn_name,
        args=fn_args,
    )

    formatted_args = json.dumps(fn_args, ensure_ascii=False)
    print(f"[PLAN] Planned irreversible action: {fn_name}({formatted_args})", file=sys.stderr)

    t0_approval = time.perf_counter()
    if AUTO_APPROVE:
        print("[APPROVAL] Auto-approved (AUTO_APPROVE=1).", file=sys.stderr)
        approved = True
    else:
        print("Approve this action? [y/N]: ", end="", file=sys.stderr, flush=True)
        try:
            user_input = sys.stdin.readline()
            if not user_input:
                # EOF / keine Eingabe zählt als Ablehnung (Task 04)
                approved = False
            else:
                approved = user_input.strip().lower() in ("y", "yes", "j", "ja")
        except Exception:
            approved = False

    approval_duration = (time.perf_counter() - t0_approval) * 1000

    log_event(
        run_id=run_id,
        event="approval_granted" if approved else "approval_denied",
        duration_ms=approval_duration,
        ok=approved,
        tool=fn_name,
        args=fn_args,
    )

    return approved


# 4. Agent Loop
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
                temperature=0,
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

                    is_irreversible = fn_name in IRREVERSIBLE_TOOLS

                    if not args_valid:
                        t0_tool = time.perf_counter()
                        tool_output = json.dumps({"error": parse_error})
                        tool_ok = False
                        tool_duration = (time.perf_counter() - t0_tool) * 1000
                    elif is_irreversible and CONDITION == "preview":
                        # Condition 'preview': Vor Ausführung Plan zeigen und einzeln fragen
                        approved = request_approval(run_id, fn_name, fn_args)
                        t0_tool = time.perf_counter()
                        if approved:
                            tool_output, tool_ok = execute_tool(fn_name, fn_args, DATA_BASE_URL)
                        else:
                            # Task 04: Ablehnung als Tool-Ergebnis an das Modell übergeben
                            tool_output = json.dumps({
                                "status": "denied",
                                "message": "User declined this action. Do not execute it and do not retry.",
                            })
                            tool_ok = False
                        tool_duration = (time.perf_counter() - t0_tool) * 1000
                    else:
                        # Lese-Tools oder Condition 'direct'
                        t0_tool = time.perf_counter()
                        tool_output, tool_ok = execute_tool(fn_name, fn_args, DATA_BASE_URL)
                        tool_duration = (time.perf_counter() - t0_tool) * 1000

                        # Condition 'direct': Bei irreversiblen Aktionen nachträglich auf stderr berichten
                        if is_irreversible and CONDITION == "direct":
                            formatted_args = json.dumps(fn_args, ensure_ascii=False)
                            status_label = "Executed" if tool_ok else "Failed"
                            print(
                                f"[REPORT] {status_label} irreversible action: {fn_name}({formatted_args})",
                                file=sys.stderr,
                            )

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