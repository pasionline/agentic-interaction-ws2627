# agentic-interaction-ws2627

Calendar agent for answering calendar queries using tool calling with OpenAI-compatible APIs (groq).

This calendar agent is designed to handle user queries related to calendar events, such as scheduling appointments, checking availability, and managing calendar entries. It leverages tool calling capabilities to interact with a calendar API and provide accurate responses based on the user's requests.

Overview of the agent's capabilities:
* Schedule appointments and meetings
* Retrieve calendar events for a given date
* Check transportation options between two locations on a specific date
* Check weather conditions for a specific date and location

## Installation

Clone the repository and install the required dependencies using pip:
```bash
pip install -r requirements.txt
```

## Execution Conditions

The agent requires the `CONDITION` environment variable to be set to either `preview` or `direct`:

- **Plan Preview (asks before irreversible actions):**
  ```bash
  CONDITION=preview python -m agent "Block one hour of buffer for me on 29 Oct 2026 at 17:00."
  ```
- **Direct Execution (executes irreversible actions without asking and reports afterward):**
  ```bash
  CONDITION=direct python -m agent "Block one hour of buffer for me on 29 Oct 2026 at 17:00."
  ```

## Reflection on Amershi et al. (2019) Guidelines

Our binary CLI approval prompt ([y/N]) violates Guideline 9/15 (Support efficient correction, Encourage granular feedback) because the user can only reject the entire proposed appointment rather than directly editing individual parameters and give more detailed feedback rather than a binary yes or no. Also, it violates Guideline 11 (Make clear why the system did what it did) and Guideline 10 (Scope services when in doubt), because there is no explanation why a tool gets called, and it doesn't ask for clarification when the user query is ambiguous.