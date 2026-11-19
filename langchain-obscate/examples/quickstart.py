"""langchain-obscate end-to-end: redact in -> LLM -> restore out.

    pip install "langchain-obscate[examples]"
    export OBSCATE_API_KEY=ob_...            # free key: https://obscate.com
    export ANTHROPIC_API_KEY=sk-ant-...      # optional; without it a local stand-in LLM is used
    python examples/quickstart.py

Costs: 1 Obscate request per model call (the free tier is 1,000 a month) and
one small LLM call if ANTHROPIC_API_KEY is set.
"""
import os
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from langchain_obscate import with_obscate


def load_env() -> None:
    """Read KEY=VALUE lines from a .env next to this file or in a parent folder (never printed)."""
    for d in [Path.cwd(), *Path(__file__).resolve().parents]:
        f = d / ".env"
        if f.is_file():
            for line in f.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


class ShowWhatTheLLMSees(BaseCallbackHandler):
    """Demo only: prints the prompt exactly as the LLM provider receives it."""
    def on_chat_model_start(self, serialized, messages, **kw):
        for m in messages[0]:
            print(f"  [{m.type}] {m.content}")


def get_llm():
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=1024), True
    # Offline stand-in so the example runs without an LLM key: it replies using what it received.
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    class StandInLLM(BaseChatModel):
        @property
        def _llm_type(self):
            return "stand-in"

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            words = messages[-1].content.split()
            email = words[words.index("(email") + 1] if "(email" in words else "?"
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                f"(stand-in LLM) We are refunding the duplicate charge and will confirm by email to {email}."))])
    return StandInLLM(), False


def main() -> None:
    load_env()
    if not os.environ.get("OBSCATE_API_KEY"):
        raise SystemExit("Set OBSCATE_API_KEY (free key at https://obscate.com) or put it in a .env file.")
    llm, real = get_llm()
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a support agent. Reply in two sentences and repeat the customer's "
                   "email address and phone number exactly as given."),
        ("human", "{ticket}"),
    ])
    chain = with_obscate(prompt | llm | StrOutputParser())   # strategy="fpe" by default

    ticket = ("Customer Ravi Kumar (email ravi.kumar@example.com phone +91 98765 43210) "
              "says card 4242 4242 4242 4242 was charged twice. Please draft a reply.")
    print("1) Your input:\n  ", ticket)
    print("2) What the LLM receives:" + ("" if real else " (stand-in LLM, set ANTHROPIC_API_KEY for a real one)"))
    answer = chain.invoke({"ticket": ticket}, config={"callbacks": [ShowWhatTheLLMSees()]})
    print("3) Restored answer for your user:\n  ", answer)


if __name__ == "__main__":
    main()
