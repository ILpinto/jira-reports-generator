# JIRA Reports Generator

Generates Jira reports for your project

### Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r jira-report/requirements.txt
```

### Configure

 - For Weekly report: Edit `configs/weekly_updated.json` 
 - For Sprint report: Edit `configs/sprint_status.json` 

### Set envs 
```bash
export JIRA_EMAIL="you@example.com" 
export JIRA_API_TOKEN="YOUR_ATLASSIAN_API_TOKEN"
export JIRA_BASE_URL="https://issues.redhat.com/"
```
## Jira Weekly Updated Report
Generate a weekly report for the tasks updated in the past 7 days:

PYTHONPATH=src python  jira-report/src/report_generator/generate_weekly_updated.py 

### Output

- `weekly_updated_raw.json` – raw API payload + count + JQL
- `weekly_updated.html` – simple clickable table

## Sprint Status Report
Generate a sprint status report (active sprint by default):

PYTHONPATH=src python  jira-report/src/report_generator/generate_sprint_status.py

### Output

- `sprint_status_raw.json` – raw API payload + count + JQL
- `sprint_status.html` – simple clickable table
