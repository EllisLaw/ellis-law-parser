import io, re, datetime as dt
import streamlit as st
import pandas as pd
import pdfplumber

# make PyMuPDF optional so builds never block
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

st.set_page_config(page_title="Ellis Law – Police Report Parser", layout="wide")
st.title("Ellis Law – Police Report Parser")
st.caption("Upload NJTR-1 / TRPD PDFs. We’ll extract injured, likely not-at-fault parties, flag commercial/fatal, and export to Excel.")

left, right = st.columns([2,1])

# ---------- PDF text extraction ----------
def read_pdf_text(file_bytes: bytes) -> str:
    # Try pdfplumber (keeps layout), fallback to PyMuPDF
    text_chunks = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for p in pdf.pages:
                text_chunks.append(p.extract_text() or "")
        txt = "\n".join(text_chunks)
        if txt.strip():
            return txt
    except Exception:
        pass
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        for page in doc:
            text_chunks.append(page.get_text("text"))
        return "\n".join(text_chunks)
    except Exception:
        return ""

# ---------- Heuristics tuned to NJTR-1 / your samples ----------
CASE_RE = re.compile(r"\b1\s*Case\s*Number\s*\n?([A-Z0-9\-]+)", re.IGNORECASE)
DEPT_RE = re.compile(r"2\s*Police Dept of\s*\n?([A-Z0-9 \-./&]+)", re.IGNORECASE)
DATE_RE = re.compile(r"4\s*Date of Crash\s*\n?(\d{2}/\d{2}/\d{2})", re.IGNORECASE)

def parse_occupants(text: str):
    results = []
    anchor = text.find("Names & Addresses of Occupants")
    if anchor == -1:
        return results
    block = text[anchor:anchor+8000]
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    for l in lines:
        if "Names & Addresses" in l or "If Deceased" in l:
            continue
        if re.match(r"^[0-9\- ]+$", l):
            continue
        if re.search(r"[A-Za-z]{2,}\s+[A-Za-z]", l):
            clean = re.sub(r"\s*-\s*", " ", l)
            clean = re.sub(r"\s{2,}", " ", clean).strip(" -")
            m = re.search(r"\b\d{2,}\b", clean)
            name_only = clean if not m else clean[:m.start()].strip()
            if 2 <= len(name_only.split()) <= 5:
                results.append({"name": name_only})
    dedup, seen = [], set()
    for r in results:
        if r["name"] not in seen:
            dedup.append(r); seen.add(r["name"])
    return dedup[:30]

def find_charged_driver_hint(text: str):
    if "136 Charge" not in text:
        return None
    idx = text.find("136 Charge")
    snippet = text[max(0, idx-600): idx+1500]
    caps = re.findall(r"\n([A-Z][A-Z \-']{4,60})\n", snippet)
    for cand in caps:
        cand = " ".join(cand.split())
        if 2 <= len(cand.split()) <= 5:
            return cand
    return None

def flag_commercial(text: str) -> bool:
    keys = ["USDOT", "TRUCK", "CARRIER", "GVWR", "PENSKE", "FREIGHT", "MC/MX", "WEIGHT >= 26,001"]
    T = text.upper()
    return any(k in T for k in keys)

def flag_fatal(text: str) -> bool:
    return "Total Killed" in text and re.search(r"\b8\s*Total\s*Killed\s*\n?([1-9])", text)

def parse_document(text: str):
    case_no = CASE_RE.search(text)
    dept = DEPT_RE.search(text)
    date = DATE_RE.search(text)
    occupants = parse_occupants(text)
    charged_hint = (find_charged_driver_hint(text) or "").upper()
    commercial = flag_commercial(text)
    fatal = bool(flag_fatal(text))

    rows = []
    for o in occupants:
        name = o["name"]
        naf = "No" if charged_hint and charged_hint in name.upper() else "Yes"
        rows.append({
            "Case Number": case_no.group(1) if case_no else "",
            "Police Dept": dept.group(1).strip() if dept else "",
            "Date of Crash": date.group(1) if date else "",
            "Name": name,
            "NotAtFault (heuristic)": naf,
            "CommercialVehicleFlag": "Yes" if commercial else "No",
            "FatalFlag": "Yes" if fatal else "No",
        })
    if not rows:
        rows.append({
            "Case Number": case_no.group(1) if case_no else "",
            "Police Dept": dept.group(1).strip() if dept else "",
            "Date of Crash": date.group(1) if date else "",
            "Name": "(no occupants parsed)",
            "NotAtFault (heuristic)": "",
            "CommercialVehicleFlag": "Yes" if commercial else "No",
            "FatalFlag": "Yes" if fatal else "No",
        })
    return rows

# ---------- UI ----------
files = left.file_uploader("Upload one or more PDF reports", type=["pdf"], accept_multiple_files=True)
if files:
    all_rows = []
    for f in files:
        text = read_pdf_text(f.read())
        all_rows.extend(parse_document(text))
    df = pd.DataFrame(all_rows)
    left.success(f"Parsed {len(files)} file(s), {len(df)} row(s).")
    st.dataframe(df, use_container_width=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as xw:
        df.to_excel(xw, index=False, sheet_name="Leads")
    right.download_button(
        "⬇️ Download Excel",
        data=buf.getvalue(),
        file_name=f"ellis_leads_{dt.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    injured_guess = (df["NotAtFault (heuristic)"] == "Yes").sum()
    right.metric("Likely Not-At-Fault (rows)", injured_guess)
    right.metric("Commercial flagged", (df["CommercialVehicleFlag"] == "Yes").sum())
    right.metric("Fatal flagged", (df["FatalFlag"] == "Yes").sum())
else:
    left.info("Drag & drop PDFs above to begin. Mobile Safari/Chrome uploads are supported.")
    right.info("The Excel download will appear here after parsing.")
