# Third-party licenses — the practice suite

Most challenges under `bench/practice/` are vendored from a third-party
repository and are **not** covered by this project's own LICENSE. Each
challenge's `PROVENANCE.md` names its source, commit and author; this file
carries the license text those challenges require to be distributed with them.

Under `bench/practice/`, Idalia Labs wrote the `chal.toml`, `expected.json` and
`PROVENANCE.md` wrappers throughout, plus **one whole challenge**: `easy-08`
(`stream-secret`, network) is original work under this project's Apache-2.0
LICENSE, not vendored, and so does not appear below — its `PROVENANCE.md` says so.

---

## csivitu/ctf-challenges

Covers `bench/practice/easy-01` through `easy-07`, `easy-09` and `easy-10` — the
challenge descriptions and every file under their `files/` directories.
(`easy-08` is original Idalia work, above; `easy-09`'s added `flag.txt` carries
the upstream challenge's own flag so its local binary has something to print.)

- Source: <https://github.com/csivitu/ctf-challenges>
- Commit: `33ef02b4ecec332c6e7d5f5511a930747645ba8f`
- Retrieved: 2026-09-05, 2026-09-08 and 2026-09-09

```
MIT License

Copyright (c) 2020 Computer Society of India - VIT University

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Individual challenge authors are credited in each `PROVENANCE.md`.
