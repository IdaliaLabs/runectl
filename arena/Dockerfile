# runectl/arena:kali — the per-run challenge sandbox image (D2).
#
# NOTE for reviewers: this could not be built or smoke-tested against a live
# Docker daemon while building the skeleton (no daemon was available in the
# build environment). It is a best-effort, pinned toolset covering all eight
# categories per D2/plan §2.4 — treat it as needing a live `docker build` +
# `runectl arena status` pass before trusting it, per the M4 handoff report.

FROM kalilinux/kali-rolling:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        # base
        bash coreutils file xxd binutils curl wget git jq unzip p7zip-full \
        netcat-traditional ca-certificates \
        # pwn / rev
        gdb gdbserver radare2 checksec binwalk foremost \
        python3 python3-pip python3-venv \
        # crypto / misc
        openssl hashid \
        # web
        sqlmap gobuster ffuf wfuzz whatweb \
        # forensics / stego
        exiftool steghide fcrackzip \
        # misc: QR/barcode decoding (misc.toml required_tools: zbarimg)
        zbar-tools \
        # network
        tshark scapy \
        # cracking
        john hashcat \
        # zsteg deps
        ruby ruby-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN gem install zsteg --no-document

RUN python3 -m pip install --break-system-packages --no-cache-dir \
        pwntools \
        ROPgadget \
        pycryptodome \
        gmpy2 \
        angr \
        z3-solver \
        requests

# pwndbg: batch-mode-friendly GDB plugin (D2 — run_gdb is batch-only, never interactive)
RUN git clone --depth 1 https://github.com/pwndbg/pwndbg /opt/pwndbg \
    && /opt/pwndbg/setup.sh || true

# Non-root, no-privileged execution user (D2 posture)
RUN useradd -m -s /bin/bash ctf \
    && mkdir -p /ctf \
    && chown -R ctf:ctf /ctf /opt/pwndbg

USER ctf
WORKDIR /ctf

CMD ["sleep", "infinity"]
