from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import psycopg2
import os
from datetime import datetime
from typing import List, Optional

app = FastAPI()

# Allow CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    # Vercel uses DATABASE_URL. Fallback for local testing.
    db_url = os.environ.get('DATABASE_URL') or os.environ.get('SUPABASE_URL', 'postgresql://postgres.ezittkutzpqhyocdmjib:xixnaGfykviznizhi5@aws-1-eu-central-1.pooler.supabase.com:6543/postgres')
    return psycopg2.connect(db_url)

def get_closest_date(conn, target_date_str):
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM calendar_dates ORDER BY date ASC")
    available_dates = [row[0] for row in cursor.fetchall()]
    cursor.close()
    
    if not available_dates:
        return None
        
    if target_date_str in available_dates:
        return target_date_str
        
    try:
        target_dt = datetime.strptime(target_date_str, "%Y%m%d")
    except ValueError:
        return available_dates[0]
        
    closest_date = None
    min_diff = None
    
    for d_str in available_dates:
        try:
            dt = datetime.strptime(d_str, "%Y%m%d")
            diff = abs((dt - target_dt).days)
            if min_diff is None or diff < min_diff:
                min_diff = diff
                closest_date = d_str
        except ValueError:
            continue
            
    return closest_date

@app.get("/api/stops")
def get_stops():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT r.route_id, s.stop_name
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN stops s ON st.stop_id = s.stop_id
        ORDER BY r.route_id, s.stop_name
    """)
    
    stops_by_line = {}
    for row in cursor.fetchall():
        route_id, stop_name = row
        if route_id not in stops_by_line:
            stops_by_line[route_id] = []
        stops_by_line[route_id].append({
            "stop_name": stop_name
        })
        
    cursor.close()
    conn.close()
    return JSONResponse(content=stops_by_line)

@app.get("/api/destinations")
def get_destinations(stop_name: str = Query(...), route_id: str = Query(...)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT DISTINCT t.trip_headsign
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN stops s ON st.stop_id = s.stop_id
        WHERE s.stop_name = %s AND t.route_id = %s
        ORDER BY t.trip_headsign ASC
    """, (stop_name, route_id))
    
    destinations = [{"trip_headsign": row[0]} for row in cursor.fetchall()]
        
    cursor.close()
    conn.close()
    return JSONResponse(content=destinations)

@app.get("/api/schedule")
def get_schedule(
    stop_name: str = Query(...),
    route_id: str = Query(...),
    trip_headsign: List[str] = Query(...),
    time: str = Query(...),
    date: Optional[str] = Query(None)
):
    conn = get_db_connection()
    
    if date:
        today_str = date
    else:
        today_str = datetime.now().strftime("%Y%m%d")
        
    resolved_date = get_closest_date(conn, today_str)
    
    if not resolved_date:
        conn.close()
        raise HTTPException(status_code=500, detail="No calendar exceptions available in database")
        
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT service_id 
        FROM calendar_dates 
        WHERE date = %s AND exception_type = 1
    """, (resolved_date,))
    active_services = [row[0] for row in cursor.fetchall()]
    
    if not active_services:
        cursor.close()
        conn.close()
        return JSONResponse(content={
            "route_id": route_id,
            "departures": [],
            "message": f"No active schedule for date {resolved_date}"
        })
        
    # Postgres IN clause with psycopg2 uses ANY(%s) for arrays, or we dynamically generate %s
    headsign_placeholders = ','.join('%s' for _ in trip_headsign)
    service_placeholders = ','.join('%s' for _ in active_services)
    
    query = f"""
        SELECT 
            r.route_id,
            r.route_short_name,
            r.route_long_name,
            st.stop_id,
            st.departure_time,
            t.trip_headsign,
            t.direction_id
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        JOIN stops s ON st.stop_id = s.stop_id
        WHERE s.stop_name = %s
          AND t.route_id = %s
          AND t.trip_headsign IN ({headsign_placeholders})
          AND t.service_id IN ({service_placeholders})
          AND st.departure_time >= %s
        ORDER BY st.departure_time ASC
        LIMIT 3
    """
    
    params_list = [stop_name, route_id] + trip_headsign + active_services + [time]
    cursor.execute(query, params_list)
    rows = cursor.fetchall()
    
    departures = []
    for row in rows:
        departures.append({
            "line": row[0],
            "line_name": row[2],
            "departure_time": row[4],
            "destination": row[5].upper() if row[5] else "UNKNOWN",
            "direction_id": row[6]
        })
        
    cursor.close()
    conn.close()
    
    return JSONResponse(content={
        "route_id": route_id,
        "stop_name": stop_name,
        "resolved_date": resolved_date,
        "departures": departures
    })
