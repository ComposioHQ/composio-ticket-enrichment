FROM composio/composio:latest

USER root
RUN chmod 755 /root/entrypoint.sh

RUN pip install swekit[langgraph]
RUN pip install -U composio-core --pre

# Create and set ownership of working directory
RUN mkdir -p /composio-ticket-enrichment
WORKDIR /composio-ticket-enrichment

COPY . .

ENTRYPOINT [ "python", "main.py" ]