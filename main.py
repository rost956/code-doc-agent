from __future__ import annotations

import argparse

from code_agent.agent import CodeResearchAgent
from code_agent.config import load_config
from code_agent.llm_client import OpenAICompatibleClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask an LLM agent questions about indexed Python code.")
    parser.add_argument("question", help="Question about the project code")
    parser.add_argument("--env", default=".env", help="Path to .env file")
    parser.add_argument("--index", default=None, help="Path to code index JSON. Overrides AGENT_INDEX_PATH")
    parser.add_argument("--log", default=None, help="Path to agent JSONL log. Overrides AGENT_LOG_PATH")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum agent tool-calling steps")
    parser.add_argument("--prompt", default=None, help="Path to system prompt text file")
    args = parser.parse_args()

    config = load_config(args.env)

    index_path = args.index or config.agent.index_path
    log_path = args.log or config.agent.log_path
    max_steps = args.max_steps or config.agent.max_steps
    system_prompt_path = args.prompt or config.agent.system_prompt_path

    client = OpenAICompatibleClient(config.llm)
    agent = CodeResearchAgent(
        index_path=index_path,
        llm_client=client,
        log_path=log_path,
        max_steps=max_steps,
        system_prompt_path=system_prompt_path,
    )

    answer = agent.answer(args.question)
    print(answer)


if __name__ == "__main__":
    main()
