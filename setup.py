"""
HedgeFusion Setup
==================
One-command installer. Run once after cloning.

    python setup.py

What it does:
  1. Checks Python version (needs 3.10+)
  2. Creates .env from .env.example if missing
  3. Installs all dependencies
  4. Creates required directories
  5. Runs a self-test (imports, paper trade sim)
  6. Prints the next steps

Usage:
    python setup.py           # full setup
    python setup.py --check   # just run checks, no install
    python setup.py --reset   # clear cache and logs, keep .env
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


ROOT = Path(__file__).parent

# ── Colours for terminal output ───────────────────────────────
def green(s):  return f"\033[32m{s}\033[0m"
def red(s):    return f"\033[31m{s}\033[0m"
def yellow(s): return f"\033[33m{s}\033[0m"
def bold(s):   return f"\033[1m{s}\033[0m"
def cyan(s):   return f"\033[36m{s}\033[0m"


def check_python():
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        print(red(f"  ✗ Python {v.major}.{v.minor} found. Need Python 3.10+"))
        print(red("    Download: https://python.org"))
        sys.exit(1)
    print(green(f"  ✓ Python {v.major}.{v.minor}.{v.micro}"))


def create_env():
    env_path  = ROOT / ".env"
    example   = ROOT / ".env.example"
    if env_path.exists():
        print(green("  ✓ .env exists"))
        return
    if example.exists():
        shutil.copy(example, env_path)
        print(yellow("  ✓ .env created from .env.example"))
        print(yellow("    → Open .env and add your OPENAI_API_KEY"))
    else:
        # Create minimal .env
        env_path.write_text(
            "OPENAI_API_KEY=sk-your-key-here\n"
            "MODEL_NAME=gpt-4o-mini\n"
            "KITE_PAPER_TRADE=true\n"
            "PORTFOLIO_SIZE_INR=500000\n"
        )
        print(yellow("  ✓ .env created (minimal)"))
        print(yellow("    → Add your OPENAI_API_KEY to .env"))


def create_directories():
    dirs = ["data", "data/cache", "logs", "outputs"]
    for d in dirs:
        p = ROOT / d
        p.mkdir(parents=True, exist_ok=True)
    print(green("  ✓ Directories created (data/, logs/, outputs/)"))


def install_dependencies(check_only: bool = False):
    req_file = ROOT / "requirements.txt"
    if not req_file.exists():
        print(red("  ✗ requirements.txt not found"))
        return False

    if check_only:
        # Just check if key packages are importable
        packages = ["openai", "yfinance", "loguru", "dotenv", "requests"]
        missing  = []
        for pkg in packages:
            try:
                __import__(pkg if pkg != "dotenv" else "dotenv")
            except ImportError:
                missing.append(pkg)
        if missing:
            print(yellow(f"  ⚠ Missing packages: {', '.join(missing)}"))
            print(yellow("    Run: pip install -r requirements.txt"))
            return False
        print(green("  ✓ All dependencies installed"))
        return True

    print("  Installing dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req_file), "-q"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(green("  ✓ Dependencies installed"))
        return True
    else:
        print(red(f"  ✗ Install failed: {result.stderr[:200]}"))
        print(yellow("    Try manually: pip install -r requirements.txt"))
        return False


def check_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        print(yellow("  ⚠ .env not found — run setup.py first"))
        return False

    from dotenv import load_dotenv
    load_dotenv(env_path)
    key = os.getenv("OPENAI_API_KEY","")
    if not key or key == "sk-your-key-here":
        print(yellow("  ⚠ OPENAI_API_KEY not set in .env"))
        print(yellow("    Get your key: https://platform.openai.com/api-keys"))
        return False
    print(green("  ✓ OPENAI_API_KEY configured"))
    return True


def run_self_test():
    """Import all modules and run a quick paper trade simulation."""
    print("\n  Running self-test...")
    import json

    # Test 1: all imports
    try:
        sys.path.insert(0, str(ROOT))
        os.environ.setdefault("OPENAI_API_KEY", "sk-test")
        os.environ.setdefault("KITE_PAPER_TRADE", "true")

        from agents.prompts import FUNDAMENTALS_PROMPT, PORTFOLIO_MANAGER_PROMPT
        from agents.runner  import parse_json_response
        from tools.india_data    import DATA_TOOL_MAP
        from tools.kite_execution import place_nse_order, _paper_mode
        from config import HOLDINGS, PORTFOLIO_SIZE_INR
        print(green("  ✓ All modules import successfully"))
    except Exception as e:
        print(red(f"  ✗ Import failed: {e}"))
        return False

    # Test 2: paper trade simulation
    try:
        import tools.india_data as m
        def mock_quote(ticker):
            return json.dumps({
                "symbol": ticker+".NS", "ticker": ticker,
                "info": {"currentPrice": 2950.0, "sector": "Energy"},
                "latest_close": 2950.0,
            })
        m.get_nse_quote = mock_quote

        from tools.kite_execution import _paper_execute
        result = json.loads(_paper_execute("RELIANCE","BUY",3,"MARKET",None,None,None))
        assert result["status"] == "PAPER_EXECUTED"
        assert result["quantity"] == 3
        print(green(f"  ✓ Paper trade simulation: {result['order_id']} @ ₹{result['fill_price']}"))
    except Exception as e:
        print(red(f"  ✗ Paper trade test failed: {e}"))
        return False

    # Test 3: config
    try:
        from config import HOLDINGS, WATCHLIST, PORTFOLIO_SIZE_INR, mode_label
        assert len(HOLDINGS) >= 5
        print(green(f"  ✓ Config: {len(HOLDINGS)} holdings, ₹{PORTFOLIO_SIZE_INR:,.0f} portfolio"))
    except Exception as e:
        print(red(f"  ✗ Config test failed: {e}"))
        return False

    # Test 4: scorer
    try:
        from multibagger_screener import score_stock
        # Mock
        result = {"score": 0, "signals": [], "flags": ["No price data"], "ticker": "TEST", "data": {}}
        print(green("  ✓ Multibagger scorer available"))
    except Exception as e:
        print(red(f"  ✗ Scorer test failed: {e}"))
        return False

    return True


def print_next_steps():
    print(f"""
  {bold('═' * 53)}
  {bold('  HEDGEFUSION SETUP COMPLETE')}
  {bold('═' * 53)}

  {bold('Quickstart:')}

  1. Open {cyan('.env')} and add your OpenAI API key:
     {yellow('OPENAI_API_KEY=sk-proj-xxxxxxxxxx')}

  2. Run your first analysis (Anaconda Prompt):
     {cyan('python hf.py')}          ← interactive menu
     {cyan('python hf.py run RELIANCE')}   ← single stock
     {cyan('python hf.py portfolio')} ← all your holdings

  {bold('Other commands:')}
     {cyan('python hf.py screen')}   ← multibagger screener
     {cyan('python hf.py risk')}     ← risk dashboard
     {cyan('python hf.py sector')}   ← sector rotation
     {cyan('python hf.py backtest')} ← signal backtester
     {cyan('python hf.py earnings')} ← earnings calendar
     {cyan('python hf.py status')}   ← system health check

  {bold('Cost estimate (gpt-4o-mini):')}
     Single stock:     ₹3–8
     Full portfolio:   ₹50–80
     Multibagger scan: ₹20–40

  {bold('Paper trading is ON by default.')}
  To go live: set KITE_PAPER_TRADE=false in .env
  and run python tools/kite_login.py each morning.

  {bold('GitHub:')} https://github.com/Zoravar31/hedge-fusion
  {bold('═' * 53)}
""")


def reset_workspace():
    print("  Clearing cache and logs...")
    for pattern in ["data/cache/*", "logs/*.csv", "logs/*.log"]:
        import glob
        for f in glob.glob(str(ROOT / pattern)):
            Path(f).unlink()
            print(f"    deleted {f}")
    print(green("  ✓ Workspace reset (outputs kept, .env kept)"))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HedgeFusion Setup")
    parser.add_argument("--check", action="store_true", help="Check only, no install")
    parser.add_argument("--reset", action="store_true", help="Clear cache and logs")
    args = parser.parse_args()

    print(f"""
  ╔══════════════════════════════════════════╗
  ║   HedgeFusion Setup                     ║
  ║   9-Agent NSE Trading System            ║
  ╚══════════════════════════════════════════╝
""")

    if args.reset:
        reset_workspace()
        return

    print("  Checking system...\n")
    check_python()
    create_directories()
    create_env()

    if not args.check:
        install_dependencies(check_only=False)
    else:
        install_dependencies(check_only=True)

    check_env()
    ok = run_self_test()

    if ok:
        print_next_steps()
    else:
        print(red("\n  Setup encountered issues. Check errors above."))
        print(yellow("  Most common fix: pip install -r requirements.txt"))


if __name__ == "__main__":
    main()
