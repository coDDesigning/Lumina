"""One-command local launcher for Lumina.

Usage:
    python run_local.py            # Runs native dev mode (FastAPI + Worker + Vite frontend)
    python run_local.py --docker   # Runs containerized stack via Docker Compose
"""

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def check_ollama():
    """Verify if Ollama is accessible."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", headers={"User-Agent": "Lumina-Launcher"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                print("  [OK] Ollama is running at http://localhost:11434")
                return True
    except Exception:
        print("  [!] Warning: Ollama is not running on http://localhost:11434.")
        print("      Make sure to start Ollama ('ollama serve') for local AI generation.")
    return False


def run_docker():
    """Start the application using Docker Compose."""
    print("\n=== Starting Lumina in Docker mode ===")
    check_ollama()
    try:
        subprocess.run(["docker", "compose", "up", "--build", "-d"], cwd=ROOT_DIR, check=True)
        port = os.getenv("LUMINA_PORT", "8085")
        print("\n[OK] Lumina containers are running!")
        print(f"    Frontend: http://localhost:{port}")
        print("    API:      http://localhost:8000/health/ready")
        time.sleep(3)
        webbrowser.open(f"http://localhost:{port}")
    except subprocess.CalledProcessError as e:
        print(f"[X] Docker Compose failed: {e}")
        sys.exit(1)


def run_native():
    """Run native local processes (FastAPI + Worker + Vite Frontend)."""
    print("\n" + "=" * 60)
    print("           Starting Lumina Local Environment")
    print("=" * 60)

    # 1. Stop conflicting Docker containers if active
    try:
        # Check if Docker compose containers are running
        res = subprocess.run(["docker", "compose", "ps", "-q"], cwd=ROOT_DIR, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            print("\n--> Stopping existing Docker containers to free local ports...")
            subprocess.run(["docker", "compose", "down"], cwd=ROOT_DIR, check=False)
            time.sleep(1)
    except Exception:
        pass

    # 2. Check frontend dependencies
    frontend_dir = ROOT_DIR / "frontend"
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    if frontend_dir.exists() and not (frontend_dir / "node_modules").exists():
        print("\n--> Installing frontend dependencies (first-time setup)...")
        try:
            subprocess.run([npm_cmd, "install"], cwd=frontend_dir, check=True)
            print("  [OK] Frontend dependencies installed.")
        except Exception as e:
            print(f"  [!] npm install warning: {e}")

    check_ollama()

    # 3. Database Migration
    print("\n--> Running database migrations...")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)

    # Load .env into env dict if dotenv is available
    try:
        import dotenv
        dotenv.load_dotenv(ROOT_DIR / ".env")
        for k, v in os.environ.items():
            env[k] = v
    except ImportError:
        pass

    try:
        subprocess.run([PYTHON_EXE, "-m", "alembic", "upgrade", "head"], cwd=ROOT_DIR, env=env, check=True)
        print("  [OK] Database is up to date.")
    except Exception as e:
        print(f"  [!] Migration warning: {e}")

    # 4. Spawn processes
    processes = []

    def cleanup(signum=None, frame=None):
        print("\n--> Shutting down all Lumina services...")
        for p in processes:
            try:
                p.terminate()
                p.kill()
            except Exception:
                pass
        print("[OK] All services stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        # Backend API
        print("\n--> Starting Backend API on http://localhost:8000...")
        api_proc = subprocess.Popen(
            [PYTHON_EXE, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--reload"],
            cwd=ROOT_DIR,
            env=env,
        )
        processes.append(api_proc)

        # Background Worker (for processing uploaded documents)
        print("--> Starting Background Document Worker...")
        worker_proc = subprocess.Popen(
            [PYTHON_EXE, "-m", "workers.document_processor"],
            cwd=ROOT_DIR,
            env=env,
        )
        processes.append(worker_proc)

        # Frontend Dev Server
        if frontend_dir.exists():
            print("--> Starting Frontend (Vite) on http://localhost:5173...")
            frontend_proc = subprocess.Popen(
                [npm_cmd, "run", "dev"],
                cwd=frontend_dir,
                env=os.environ.copy(),
            )
            processes.append(frontend_proc)

        print("\n" + "=" * 60)
        print("  [OK] Lumina is running!")
        print("      Frontend UI: http://localhost:5173")
        print("      Backend API: http://localhost:8000")
        print("      API Docs:    http://localhost:8000/docs")
        print("=" * 60)
        print("\nPress Ctrl+C at any time to stop all services.\n")

        # Open browser after a brief pause
        time.sleep(2)
        webbrowser.open("http://localhost:5173")

        # Keep parent alive and monitor child processes
        while True:
            for p in processes:
                ret = p.poll()
                if ret is not None and ret != 0:
                    print(f"[!] Process {p.args} exited unexpectedly with code {ret}")
            time.sleep(1)

    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lumina local runner")
    parser.add_argument("--docker", action="store_true", help="Run via Docker Compose instead of native dev")
    args = parser.parse_args()

    if args.docker:
        run_docker()
    else:
        run_native()
