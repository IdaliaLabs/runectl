# runectl/arena:kali — the per-run challenge sandbox image (D2).
#
# Built and smoke-tested against a live Docker daemon: first on 2026-09-07, and
# rebuilt 2026-09-09 for M7's expanded toolset (all eight categories). `runectl
# arena build` succeeds on amd64 and the ten-challenge bench ran challenges
# inside this image. When you add or drop a package, rebuild and re-run
# `runectl arena status` — the label fingerprint changes and an existing image
# will report as stale (D17) until you do.

FROM kalilinux/kali-rolling:latest

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        # base
        bash coreutils file xxd binutils curl wget git jq unzip unrar p7zip-full \
        netcat-traditional ca-certificates \
        # build toolchain — Kali ships Python 3.14, and several pinned libs below
        # (unicorn via angr, gmpy2) have no cp314 wheel yet, so pip compiles them
        # from source. Carried from the predecessor's Dockerfile, which needed
        # exactly this set. Dropping any of these fails the pip layer.
        cmake pkg-config python3-dev \
        libgmp-dev libmpfr-dev libmpc-dev libssl-dev libffi-dev \
        # pwn / rev (rizin: modern radare2 fork; ltrace/strace: catch the easy
        # strcmp/syscall wins before reaching for angr; upx-ucl: unpack packed
        # binaries — binary is `upx`)
        gdb gdbserver radare2 rizin ltrace strace upx-ucl checksec binwalk foremost \
        python3 python3-pip python3-venv \
        # crypto / misc
        openssl hashid \
        # web
        sqlmap gobuster ffuf wfuzz whatweb \
        # forensics / stego (sleuthkit: disk-image mmls/fls/icat/tsk_recover;
        # poppler-utils: pdftotext/pdfdetach/pdfinfo; sox/ffmpeg: audio +
        # spectrograms; multimon-ng: DTMF/POCSAG decode)
        exiftool steghide fcrackzip sleuthkit poppler-utils \
        sox ffmpeg multimon-ng \
        # misc: QR/barcode decoding (misc.toml required_tools: zbarimg)
        zbar-tools \
        # osint (whois; dnsutils provides dig/nslookup — mostly offline metadata,
        # but network=bridge by default so live lookups work when reachable)
        whois dnsutils \
        # network (capinfos ships in wireshark-common; nmap: live targets;
        # aircrack-ng: WEP/WPA capture cracking)
        tshark wireshark-common scapy nmap aircrack-ng \
        # cracking
        john hashcat \
        # zsteg deps
        ruby ruby-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

# zsteg: PNG/BMP LSB stego. one_gadget: single-gadget RCE addresses for pwn ROP.
RUN gem install zsteg one_gadget --no-document

RUN python3 -m pip install --break-system-packages --no-cache-dir \
        pwntools \
        ROPgadget \
        pycryptodome \
        gmpy2 \
        angr \
        z3-solver \
        volatility3 \
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
