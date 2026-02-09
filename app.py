from fastapi import FastAPI, UploadFile, File, HTTPException
from typing import List
import pandas as pd
import io
import re
from decimal import Decimal

# שימו לב: המשתנה נקרא 'app' כדי שהפקודה ב-Render תעבוד
app = FastAPI(title="Aviation Invoice Audit API", version="1.0")

# --- CORE LOGIC (הלוגיקה שלנו) ---

def detect_charge_type(filename):
    fname = filename.upper()
    if fname.startswith('AIC'): return 'Shanwick/Oceanic'
    elif fname.startswith('M') or fname.startswith('B'): return 'Terminal/Other'
    elif fname.startswith('A'): return 'Route Charges'
    return 'Unknown'

def parse_eurocontrol_line(line_str):
    try:
        if len(line_str) < 10: return None
        # המרה בטוחה לטקסט
        if isinstance(line_str, bytes):
            line = line_str.decode('utf-8', errors='ignore')
        else:
            line = line_str
            
        if line[7:9] != '01': return None

        # 1. תאריך
        flight_date = line[9:19].replace('/', '-')
        
        # 2. מספר טיסה
        callsign = line[25:35].split()[0].strip()

        # 3. מסלול
        route_match = re.search(r'([A-Z]{4}[A-Z]{4})', line[35:55])
        if route_match:
            route_block = route_match.group(1)
            dep_icao = route_block[0:4]
            arr_icao = route_block[4:8]
        else:
            dep_icao = line[38:42].strip()
            arr_icao = line[42:46].strip()

        # 4. רישום
        reg = None
        reg_match = re.search(r'(4X-?[A-Z]{3}|N[0-9]{1,5}[A-Z]{0,2})', line)
        if reg_match:
            reg = reg_match.group(1).replace('-', '')
        else:
            reg = 'UNKNOWN'

        # 5. סכום
        amount = Decimal("0.00")
        amount_zone = line[35:]
        decimal_matches = re.findall(r'(\d+,\d+)', amount_zone)
        candidates = []
        for m in decimal_matches:
            val = Decimal(m.replace(',', '.'))
            if val > 0: candidates.append(val)
        
        if not candidates:
            int_matches = re.findall(r'\s(\d+)\s', amount_zone)
            for m in int_matches:
                val = Decimal(m)
                if val > 0: candidates.append(val)
        if candidates:
            amount = candidates[0]

        return {
            'euro_date': flight_date,
            'euro_callsign': callsign,
            'euro_reg': reg,
            'euro_dep': dep_icao,
            'euro_arr': arr_icao,
            'euro_amount': float(amount),
            'raw_line': line.strip()
        }
    except Exception:
        return None

# --- API ENDPOINTS ---

@app.get("/")
def read_root():
    return {"status": "System Operational", "message": "Eurocontrol Audit Engine is Live"}

@app.post("/audit/eurocontrol")
async def audit_eurocontrol(
    leon_file: UploadFile = File(...),
    euro_files: List[UploadFile] = File(...)
):
    # 1. עיבוד יורוקונטרול
    euro_records = []
    for file in euro_files:
        content = await file.read()
        # פיצול לשורות בצורה בטוחה
        decoded_content = content.decode('utf-8', errors='ignore')
        lines = decoded_content.splitlines()
        c_type = detect_charge_type(file.filename)
        
        for line in lines:
            parsed = parse_eurocontrol_line(line)
            if parsed:
                parsed['source_file'] = file.filename
                parsed['charge_type'] = c_type
                euro_records.append(parsed)
    
    if not euro_records:
        raise HTTPException(status_code=400, detail="No valid flight lines found")
        
    euro_df = pd.DataFrame(euro_records)

    # 2. עיבוד לאון
    try:
        leon_content = await leon_file.read()
        if leon_file.filename.endswith('.csv'):
            leon_df = pd.read_csv(io.BytesIO(leon_content))
        else:
            leon_df = pd.read_excel(io.BytesIO(leon_content))
            
        leon_df.columns = [c.split('[')[0].strip() for c in leon_df.columns]
        leon_df['Date ADEP'] = pd.to_datetime(leon_df['Date ADEP'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
        
        if 'Aircraft' in leon_df.columns:
            leon_df['Aircraft_Clean'] = leon_df['Aircraft'].astype(str).str.replace('-', '').str.replace(' ', '')
        
        if 'Flight number' in leon_df.columns:
            leon_df['Flight_Clean'] = leon_df['Flight number'].astype(str).str.strip()
        else:
            leon_df['Flight_Clean'] = ''

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error reading Leon file: {str(e)}")

    # 3. מנוע ההתאמה
    euro_df['KEY_REG'] = (euro_df['euro_date'] + '_' + euro_df['euro_reg'] + '_' + euro_df['euro_dep'] + '_' + euro_df['euro_arr'])
    leon_df['KEY_REG'] = (leon_df['Date ADEP'] + '_' + leon_df['Aircraft_Clean'] + '_' + leon_df['ADEP ICAO'] + '_' + leon_df['ADES ICAO'])
    
    euro_df['KEY_FLT'] = (euro_df['euro_date'] + '_' + euro_df['euro_callsign'] + '_' + euro_df['euro_dep'] + '_' + euro_df['euro_arr'])
    leon_df['KEY_FLT'] = (leon_df['Date ADEP'] + '_' + leon_df['Flight_Clean'] + '_' + leon_df['ADEP ICAO'] + '_' + leon_df['ADES ICAO'])
    
    lookup_reg = leon_df.set_index('KEY_REG')['Trip number'].to_dict()
    lookup_flt = leon_df.set_index('KEY_FLT')['Trip number'].to_dict()

    euro_df['LEON_TRIP_ID'] = euro_df['KEY_REG'].map(lookup_reg)
    euro_df.loc[euro_df['LEON_TRIP_ID'].isna(), 'LEON_TRIP_ID'] = euro_df['KEY_FLT'].map(lookup_flt)
    
    euro_df['MATCH_STATUS'] = 'Unmatched'
    euro_df.loc[euro_df['LEON_TRIP_ID'].notna(), 'MATCH_STATUS'] = 'Matched'

    return {
        "stats": {
            "total_rows": len(euro_df),
            "matched_rows": len(euro_df[euro_df['MATCH_STATUS'] == 'Matched']),
            "total_amount_eur": euro_df['euro_amount'].sum()
        },
        "data": euro_df.to_dict(orient="records")
    }
