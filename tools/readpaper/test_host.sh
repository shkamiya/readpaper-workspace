#!/bin/bash
# Smoke-test the native host without Chrome.
#
#   test_host.sh <url> [--run]        feed a framed native-messaging message and
#                                     decode the reply (--run executes the real job)
#   test_host.sh --resolve-only [url...]
#                                     just print resolve() -> (pdf_url, dest, how)
#                                     for the given urls, or for a default battery
#                                     covering arXiv / OpenReview / PMLR / JMLR /
#                                     ACL / NeurIPS / raw .pdf
#
# Without --run, readpaper_run.sh is stubbed out (we only check URL resolution
# and the "started"/"error" reply). With --run, the real job executes.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="$ROOT/host/readpaper_host.py"

if [ "${1:-}" = "--resolve-only" ]; then
  shift
  python3 - "$HOST" "$@" <<'PY'
import importlib.util, sys

spec = importlib.util.spec_from_file_location("readpaper_host", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

urls = sys.argv[2:] or [
    "https://arxiv.org/abs/2605.26106v1",
    "https://arxiv.org/pdf/2402.01107v3",
    "https://openreview.net/forum?id=YicbFdNTTy",
    "https://proceedings.mlr.press/v235/kim24d.html",       # pdf lives on github raw
    "http://proceedings.mlr.press/v37/ioffe15.html",        # old flat layout
    "https://www.jmlr.org/papers/v21/19-1015.html",
    "https://aclanthology.org/2021.emnlp-main.446/",
    "https://papers.nips.cc/paper_files/paper/2017/hash/"
    "3f5ee243547dee91fbd053c1c4a845aa-Abstract.html",
    "https://proceedings.mlr.press/v37/ioffe15.pdf",        # raw pdf url
    "https://example.com/not-a-paper",                      # expected: failure
]
for u in urls:
    pdf, dest, how = mod.resolve({"url": u})
    if pdf:
        print(f"OK   {u}\n     -> dest={dest}\n        pdf ={pdf}   [{how}]")
    else:
        print(f"FAIL {u}\n     -> {how}")
PY
  exit $?
fi

URL="${1:?usage: test_host.sh <url> [--run]  |  test_host.sh --resolve-only [url...]}"

python3 - "$HOST" "$URL" "${2:-}" <<'PY'
import json, struct, subprocess, sys, os, tempfile

host, url, flag = sys.argv[1], sys.argv[2], sys.argv[3]
env = dict(os.environ)

# Stub readpaper_run.sh unless --run, so the smoke test stays cheap.
if flag != "--run":
    stub_dir = tempfile.mkdtemp()
    stub = os.path.join(stub_dir, "readpaper_run.sh")
    with open(stub, "w") as f:
        f.write("#!/bin/bash\necho \"[stub] would run: $*\" >&2\n")
    os.chmod(stub, 0o755)
    # readpaper_host.py resolves RUN as <its dir>/readpaper_run.sh, so point it
    # at the stub by temporarily shadowing via a symlinked copy of the host.
    import shutil
    host_copy = os.path.join(stub_dir, "readpaper_host.py")
    shutil.copy(host, host_copy)
    host = host_copy

payload = json.dumps({"url": url}).encode()
framed = struct.pack("<I", len(payload)) + payload

proc = subprocess.run([sys.executable, host], input=framed,
                      capture_output=True, env=env)
out = proc.stdout
if len(out) < 4:
    print("NO REPLY. stderr:", proc.stderr.decode(errors="replace"))
    sys.exit(1)
n = struct.unpack("<I", out[:4])[0]
reply = json.loads(out[4:4+n].decode())
print("reply:", json.dumps(reply, ensure_ascii=False))
if proc.stderr:
    print("stderr:", proc.stderr.decode(errors="replace").strip())
PY
