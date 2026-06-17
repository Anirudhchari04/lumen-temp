"""Build a correctly-rooted deploy zip for Azure App Service (Oryx Python build).

Includes requirements.txt + the runtime dirs at the zip root so Oryx detects
the Python platform. Excludes node_modules, frontend/src, logs, caches.
"""
import os
import zipfile

INC_DIRS = ["app", "v2", "frontend/dist", "public", "data", "scripts"]
INC_FILES = ["requirements.txt"]
OUT = "deploy-new.zip"


def main():
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for f in INC_FILES:
            z.write(f, f)
            n += 1
        for d in INC_DIRS:
            for dp, dn, fns in os.walk(d):
                dn[:] = [x for x in dn if x != "__pycache__"]
                for fn in fns:
                    if fn.endswith(".pyc"):
                        continue
                    full = os.path.join(dp, fn)
                    arc = full.replace(os.sep, "/")
                    z.write(full, arc)
                    n += 1
    print("files:", n, "| zip MB:", round(os.path.getsize(OUT) / 1e6, 2))
    with zipfile.ZipFile(OUT) as z:
        names = set(z.namelist())
    for must in ["requirements.txt", "app/main.py", "v2/router.py",
                 "v2/orchestrator.py", "frontend/dist/index.html"]:
        print(("OK  " if must in names else "MISSING  ") + must)


if __name__ == "__main__":
    main()
