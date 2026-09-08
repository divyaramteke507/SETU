"""
SETU SIH 2026 — Offline Judge Demo Package Builder.

Assembles a clean, standalone, offline release directory:
    release/SETU-SIH-DEMO/

Contents:
- START_SETU.bat (Windows batch launcher)
- README_JUDGE.txt (Judge instructions)
- backend/ (FastAPI application code, schemas, routers, services, gazetteer)
- frontend/dist/ (Pre-built production React assets)
- model/ (Offline Hugging Face cache containing paraphrase-multilingual-MiniLM-L12-v2)

Excluded:
- Git files (.git, .gitignore)
- Python virtualenvs (venv, .venv)
- Node modules (node_modules)
- Cache directories (__pycache__, .pytest_cache)
- Database runtime files (*.db, *.sqlite)
- Log files (*.log, *.err)
"""

import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASE_DIR = os.path.join(BASE_DIR, "release", "SETU-SIH-DEMO")
HOST_HF_CACHE = os.path.join(
    os.environ.get("USERPROFILE", ""),
    ".cache",
    "huggingface",
    "hub",
    "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
)


def print_step(msg: str):
    print(f"[SETU-PACKAGE] {msg}")


def copy_backend():
    print_step("Copying clean backend source...")
    src_backend = os.path.join(BASE_DIR, "backend")
    dst_backend = os.path.join(RELEASE_DIR, "backend")
    os.makedirs(dst_backend, exist_ok=True)

    # Root files
    for fname in [
        "main.py",
        "config.py",
        "database.py",
        "models.py",
        "schemas.py",
        "seed_data.py",
        "requirements.txt",
        "__init__.py",
    ]:
        s = os.path.join(src_backend, fname)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(dst_backend, fname))

    # Directories
    def ignore_caches(src, names):
        return [n for n in names if n in {"__pycache__", ".pytest_cache", "venv", ".venv"} or n.endswith((".db", ".pyc"))]

    for dname in ["gazetteer", "routers", "services"]:
        s = os.path.join(src_backend, dname)
        d = os.path.join(dst_backend, dname)
        if os.path.isdir(d):
            shutil.rmtree(d)
        shutil.copytree(s, d, ignore=ignore_caches)


def copy_frontend():
    print_step("Copying built frontend distribution (frontend/dist)...")
    src_dist = os.path.join(BASE_DIR, "frontend", "dist")
    dst_dist = os.path.join(RELEASE_DIR, "frontend", "dist")

    if not os.path.isdir(src_dist):
        raise RuntimeError(f"Frontend dist not found at {src_dist}. Run 'npm run build' first.")

    if os.path.isdir(dst_dist):
        shutil.rmtree(dst_dist)
    shutil.copytree(src_dist, dst_dist)


def copy_launchers():
    print_step("Copying launchers and judge documentation...")
    shutil.copy2(os.path.join(BASE_DIR, "START_SETU.bat"), os.path.join(RELEASE_DIR, "START_SETU.bat"))
    shutil.copy2(os.path.join(BASE_DIR, "README_JUDGE.txt"), os.path.join(RELEASE_DIR, "README_JUDGE.txt"))


def copy_offline_model(include_model: bool = True):
    if not include_model:
        print_step("Skipping model copy (include_model=False).")
        return

    dst_model_hub = os.path.join(
        RELEASE_DIR,
        "model",
        "hub",
        "models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
    )

    if os.path.isdir(dst_model_hub):
        print_step("Model already bundled in release package.")
        return

    if not os.path.isdir(HOST_HF_CACHE):
        print_step(f"WARNING: Host model cache not found at: {HOST_HF_CACHE}")
        return

    print_step(f"Bundling offline embedding model into {dst_model_hub} (takes ~10s)...")
    os.makedirs(os.path.dirname(dst_model_hub), exist_ok=True)
    shutil.copytree(HOST_HF_CACHE, dst_model_hub)
    print_step("Model bundled successfully.")


def copy_runtime():
    print_step("Assembling self-contained portable Python runtime in runtime/...")
    src_python = r"C:\Python314"
    src_site_packages = os.path.join(BASE_DIR, "backend", "venv", "Lib", "site-packages")
    dst_runtime = os.path.join(RELEASE_DIR, "runtime")

    os.makedirs(dst_runtime, exist_ok=True)

    # 1. Base executables and runtime DLLs
    for fname in [
        "python.exe",
        "pythonw.exe",
        "python3.dll",
        "python314.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
    ]:
        s = os.path.join(src_python, fname)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(dst_runtime, fname))

    # 2. CPython core DLLs directory
    dst_dlls = os.path.join(dst_runtime, "DLLs")
    if not os.path.isdir(dst_dlls):
        print_step("Copying Python core DLLs...")
        shutil.copytree(os.path.join(src_python, "DLLs"), dst_dlls)

    # 3. Standard library (Lib)
    dst_lib = os.path.join(dst_runtime, "Lib")
    os.makedirs(dst_lib, exist_ok=True)
    src_lib = os.path.join(src_python, "Lib")
    for item in os.listdir(src_lib):
        if item in {"site-packages", "__pycache__"}:
            continue
        s = os.path.join(src_lib, item)
        d = os.path.join(dst_lib, item)
        if os.path.isdir(s):
            if not os.path.isdir(d):
                shutil.copytree(
                    s,
                    d,
                    ignore=lambda src, names: [n for n in names if n == "__pycache__" or n.endswith(".pyc")],
                )
        elif not os.path.isfile(d):
            shutil.copy2(s, d)

    # 4. Copy installed site-packages (excluding __pycache__ and .pyc to eliminate hardcoded paths)
    dst_site_packages = os.path.join(dst_lib, "site-packages")
    if not os.path.isdir(dst_site_packages):
        print_step("Copying site-packages to runtime/Lib/site-packages (excluding .pyc)...")
        shutil.copytree(
            src_site_packages,
            dst_site_packages,
            ignore=lambda src, names: [n for n in names if n == "__pycache__" or n.endswith(".pyc")],
        )

    # 5. Remove legacy backend/venv from release package if present
    old_venv = os.path.join(RELEASE_DIR, "backend", "venv")
    if os.path.isdir(old_venv):
        print_step("Removing legacy backend/venv from release package...")
        shutil.rmtree(old_venv)


def build_package(bundle_model: bool = True):
    print("=" * 70)
    print("SETU — Building Offline SIH Judge Demo Package (Zero-Install)")
    print(f"Destination: {RELEASE_DIR}")
    print("=" * 70)

    os.makedirs(RELEASE_DIR, exist_ok=True)
    copy_backend()
    copy_frontend()
    copy_launchers()
    copy_offline_model(include_model=bundle_model)
    copy_runtime()

    print("=" * 70)
    print("[SUCCESS] Zero-Install Judge Demo Package assembled successfully!")
    print(f"Location: {RELEASE_DIR}")
    print("To launch: Double-click START_SETU.bat inside that folder.")
    print("=" * 70)


if __name__ == "__main__":
    bundle = "--no-model" not in sys.argv
    build_package(bundle_model=bundle)
