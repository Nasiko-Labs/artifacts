"""Upload entrypoint (the platform's upload validator looks for src/__main__.py).

Run as `python src/__main__.py`: the script's directory (src/) goes on sys.path,
so the agent's flat imports (`from main import main`) resolve. Listens on PORT,
default 8000.
"""

from main import main

if __name__ == "__main__":
    main()
