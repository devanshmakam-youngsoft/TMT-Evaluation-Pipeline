FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./eval_automation/requirements.txt
RUN pip install --no-cache-dir -r ./eval_automation/requirements.txt

COPY . ./eval_automation

CMD ["python", "-m", "eval_automation.watch_and_run"]
