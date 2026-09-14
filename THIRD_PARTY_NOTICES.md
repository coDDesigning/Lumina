# Lumina Third-Party Notices

This file inventories direct runtime components distributed with Lumina.
Versions are controlled by `requirements.in`, `requirements.txt`,
`frontend/package-lock.json`, the pinned container images, and the embedding
model registry. Transitive dependencies and their licence files remain present
in installed Python distributions and base-image package records. Review this
file and the generated lockfiles whenever a runtime dependency or bundled asset
changes.

Lumina's AGPL-3.0-only licence does not replace the licences below. Copyright
remains with the respective authors.

## Python runtime

| Component | Licence | Upstream |
| --- | --- | --- |
| `alembic` | MIT | <https://github.com/sqlalchemy/alembic> |
| `bcrypt` | Apache-2.0 | <https://github.com/pyca/bcrypt> |
| `boto3` | Apache-2.0 | <https://github.com/boto/boto3> |
| `charset-normalizer` | MIT | <https://github.com/jawah/charset_normalizer> |
| `chromadb` | Apache-2.0 | <https://github.com/chroma-core/chroma> |
| `cryptography` | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography> |
| `email-validator` | Unlicense | <https://github.com/JoshData/python-email-validator> |
| `fastapi` | MIT | <https://github.com/fastapi/fastapi> |
| `fastembed` | Apache-2.0 | <https://github.com/qdrant/fastembed> |
| `google-genai` | Apache-2.0 | <https://github.com/googleapis/python-genai> |
| `httpx` | BSD-3-Clause | <https://github.com/encode/httpx> |
| `idna` | BSD-3-Clause | <https://github.com/kjd/idna> |
| `openai` | Apache-2.0 | <https://github.com/openai/openai-python> |
| `anthropic` | MIT | <https://github.com/anthropics/anthropic-sdk-python> |
| `pgvector` | MIT | <https://github.com/pgvector/pgvector-python> |
| `psycopg` and `psycopg-binary` | LGPL-3.0-only | <https://github.com/psycopg/psycopg> |
| `pydantic` | MIT | <https://github.com/pydantic/pydantic> |
| `pyjwt` | MIT | <https://github.com/jpadilla/pyjwt> |
| `pymupdf` | AGPL-3.0-only or commercial | <https://github.com/pymupdf/PyMuPDF> |
| `python-multipart` | Apache-2.0 | <https://github.com/Kludex/python-multipart> |
| `sqlalchemy` | MIT | <https://github.com/sqlalchemy/sqlalchemy> |
| `starlette` | BSD-3-Clause | <https://github.com/Kludex/starlette> |
| `tenacity` | Apache-2.0 | <https://github.com/jd/tenacity> |
| `uvicorn` | BSD-3-Clause | <https://github.com/Kludex/uvicorn> |

PyMuPDF is used under its GNU Affero General Public License version 3 option,
which is compatible with Lumina's AGPL-3.0-only distribution. Python wheel
licence files installed under each package's `.dist-info` directory are part of
the container image and must not be removed.

## Browser runtime

| Component | Licence | Notice / upstream |
| --- | --- | --- |
| `katex` | MIT | Copyright (c) 2013-2020 Khan Academy and other contributors; <https://github.com/KaTeX/KaTeX> |
| `lucide-react` | ISC | Feather portions copyright (c) Cole Bemis 2013-2022; remaining Lucide portions copyright (c) Lucide Contributors 2022; <https://github.com/lucide-icons/lucide> |
| `react` and `react-dom` | MIT | Copyright Meta Platforms, Inc. and affiliates; <https://github.com/facebook/react> |
| `react-router-dom` | MIT | Copyright (c) React Training LLC 2015-2019, Remix Software Inc. 2020-2021, and Shopify Inc. 2022-2023; <https://github.com/remix-run/react-router> |

The browser bundle is an aggregate of Lumina and these independently licensed
libraries. Their licence texts are available in their upstream repositories and
in the package tarballs recorded by `frontend/package-lock.json`.

### MIT notice for KaTeX, React, React DOM, React Router, and the embedding model

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the “Software”), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The copyright notices above, the embedding-model copyright notice below, and
this permission notice shall be included in all copies or substantial portions
of the Software.

THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

### ISC notice for Lucide

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the Lucide
copyright notice above and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED “AS IS” AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM
LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR
OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
PERFORMANCE OF THIS SOFTWARE.

## Bundled tools and assets

| Component | Licence | Notice / upstream |
| --- | --- | --- |
| Tesseract OCR and English trained data | Apache-2.0 | Copyright the Tesseract authors; Debian package notices remain in `/usr/share/doc`; <https://github.com/tesseract-ocr/tesseract> |
| Outfit font | SIL-OFL-1.1 | Copyright 2021 The Outfit Project Authors; <https://github.com/Outfitio/Outfit-Fonts> |
| `intfloat/multilingual-e5-large` model | MIT | Copyright Microsoft Corporation and model contributors; <https://huggingface.co/intfloat/multilingual-e5-large> |

The Outfit licence is shipped in `THIRD_PARTY_LICENSES/OFL-1.1.txt`. The
embedding model is covered by the MIT notice above; this notices file ships in
the container image under `/usr/share/doc/lumina/`. Brand artwork under `frontend/src/assets` is
original Lumina material unless a file-specific notice says otherwise.

## Licence text locations

- Lumina and PyMuPDF AGPL terms: repository root `LICENSE`.
- Apache-2.0: <https://www.apache.org/licenses/LICENSE-2.0>
- BSD-3-Clause: <https://opensource.org/license/bsd-3-clause>
- ISC: <https://opensource.org/license/isc-license-txt>
- LGPL-3.0-only: <https://www.gnu.org/licenses/lgpl-3.0.html>
- MIT: <https://opensource.org/license/mit>
- SIL-OFL-1.1: <https://openfontlicense.org/open-font-license-official-text/>
- Unlicense: <https://unlicense.org/>

Effective: 12 September 2026  
Revision: 1.0
