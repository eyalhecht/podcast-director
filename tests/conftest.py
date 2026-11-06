import os

# The backend builds its API clients on import; tests replace every call, so any key works.
os.environ.setdefault("DEEPGRAM_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
