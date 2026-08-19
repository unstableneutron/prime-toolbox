# Third-party notices and provenance

`guarded_file_ops` is original integration code informed by the following
projects and public engineering material.

## Hermes Agent

The bounded-read behavior, failure wording, hostile-file test shapes, notebook
output policy, and session-ledger concepts were adapted from current Hermes
Agent behavior and these commits:

- `0e63ed1feb569aaf826c788bc227ab4827e784f4`
- `fd452e26e3c0377d5e28f931149278957c73ccd8`
- `893792c9934447e80f40519437e7c64b4479fc3a`
- `e0b50059857fa839d36f06bab0615512f3ae0d2d`
- `faa188bf52dc59e4cfcb8e6a0701456fb623b6d0`
- `2278056256be5c31e6e92e7b58af92cbceb642b1`
- `a607b762821b6cabebf9506d0772623cfa32a328`
- `e3f8347be30a068b91662818a70d0c3c42513b96`
- `ee59ef1946e97200f4b5570acec9271f86046ab4`

Hermes Agent is MIT licensed:

> Copyright (c) 2025 Nous Research

## Firecrawl PDF Inspector

PDF support calls the separately distributed `pdf-inspector==0.2.6` Python
package and preserves its public classification, layout, encoding, per-page
Markdown, and OCR-routing fields. No PDF Inspector source is vendored.

PDF Inspector is MIT licensed:

> Copyright (c) 2026 Firecrawl

## Firecrawl Anydoc

Structured-document support calls the separately distributed
`firecrawl-anydoc==0.1.7` package. Small deterministic fixture bytes in the
repository tests come from its robustness corpus at release v0.1.7.

Anydoc is MIT licensed:

> Copyright (c) 2026 Sideguide Technologies Inc.

## LobeHub notebook provenance

Notebook behavior was also informed by the public design and test notes in
LobeHub pull request #17855, merged as
`bedee6b33203ee31b598e8b045e748ca45b88a4e`, and by Hermes' MIT-licensed
independent port in commit `a607b762821b6cabebf9506d0772623cfa32a328`.
No LobeHub source code is copied; this is behavioral provenance only. LobeHub
uses the LobeHub Community License.

## Other engineering references

Command Code's “The Read Tool Is a Systems Problem” analysis informed the
combined line, byte, per-line, Unicode recovery, explicit EOF, and hostile-path
acceptance criteria. It is referenced as engineering documentation; no source
code or prose is copied.

## MIT license text for referenced MIT-licensed material

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
