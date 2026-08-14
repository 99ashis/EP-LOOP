import os
import pandas as pd
from datetime import datetime, timedelta
from nsepython import get_bhavcopy
import time
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

# =====================================================================
# SUPABASE CONFIGURATION
# Format: postgresql://[USER]:[PASSWORD]@[HOST]:[PORT]/[DBNAME]
# =====================================================================
DB_CONNECTION_STRING = os.getenv("DB_CONNECTION_STRING")

def get_db_engine():
    if not DB_CONNECTION_STRING:
        print("❌ Error: DB_CONNECTION_STRING environment variable is not set.")
        return None
    try:
        engine = create_engine(DB_CONNECTION_STRING)
        return engine
    except Exception as e:
        print(f"Error creating DB engine: {e}")
        return None

def download_bhavcopies_to_db(lookback_days=None):
    """Downloads NSE Bhavcopies for the last N calendar days and saves them to Supabase PostgreSQL."""
    if lookback_days is None:
        lookback_days = int(os.getenv("LOOKBACK_DAYS", "3"))
    
    engine = get_db_engine()
    if not engine:
        return

    print(f"===============================================================")
    print(f"   NSE BHAVCOPY TO SUPABASE (LOOKBACK: {lookback_days} DAYS) ")
    print(f"===============================================================")
    
    current_date = datetime.now()
    success_count = 0
    
    for i in range(lookback_days):
        target_date = current_date - timedelta(days=i)
        date_str = target_date.strftime("%d-%m-%Y")
        
        # 1. Check for Weekend
        if target_date.weekday() >= 5:
            print(f"[{i+1}/{lookback_days}] {date_str}: 🛑 Weekend")
            continue

        print(f"[{i+1}/{lookback_days}] {date_str}: Fetching data...", end=" ", flush=True)
        
        try:
            df = get_bhavcopy(date_str)
            
            if df is not None and not df.empty:
                # 2. Cleanup & Formatting
                df.columns = [c.strip().upper() for c in df.columns]
                # Ensure DATE column is in YYYY-MM-DD format for Postgres
                target_date_db = target_date.strftime("%Y-%m-%d")
                df['DATE'] = target_date_db
                
                # Standardize columns to match project conventions
                mapping = {
                    'CLOSE_PRICE': 'CLOSE', 
                    'TTL_TRD_QNTY': 'VOLUME', 
                    'HIGH_PRICE': 'HIGH', 
                    'LOW_PRICE': 'LOW', 
                    'PREV_CLOSE': 'PREV',
                    'OPEN_PRICE': 'OPEN'
                }
                df = df.rename(columns=mapping)

                # 3. Deduplication (Date and Symbol)
                # Fetch existing symbols for this date to avoid duplicates
                try:
                    existing_symbols_query = text("SELECT \"SYMBOL\" FROM bhavcopies WHERE \"DATE\" = :date_val")
                    with engine.connect() as conn:
                        existing_df = pd.read_sql(existing_symbols_query, conn, params={"date_val": target_date_db})
                        if not existing_df.empty:
                            existing_symbols = set(existing_df['SYMBOL'].unique())
                            df = df[~df['SYMBOL'].isin(existing_symbols)]
                except Exception as e:
                    # If table doesn't exist, we continue with full df
                    pass
                
                if df.empty:
                    print("✅ Already up to date (0 new records)")
                    success_count += 1
                    continue

                # 4. Save to SQL
                df.to_sql('bhavcopies', engine, if_exists='append', index=False)
                
                print(f"✅ Saved {len(df)} new records")
                success_count += 1
            else:
                print("⚠️ Market Holiday")
                
            time.sleep(1)
            
        except Exception as e:
            if "404" in str(e):
                print("⚠️ Data not available")
            else:
                print(f"❌ Failed: {e}")
                if "403" in str(e) or "429" in str(e):
                    time.sleep(10)

    print(f"===============================================================")
    print(f" SYNC COMPLETE: {success_count} trading days processed")
    print(f"===============================================================")

if __name__ == "__main__":
    download_bhavcopies_to_db(3)
