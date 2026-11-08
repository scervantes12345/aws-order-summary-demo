"""Amazon Bedrock AgentCore Runtime entry point for the Sales Anomaly Investigator.

Deploy this file together with the sales_investigator/ package; see README.md.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from sales_investigator.agentcore import handle_invocation

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    return handle_invocation(payload)


if __name__ == '__main__':
    app.run()
