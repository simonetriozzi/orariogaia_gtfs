#!/usr/bin/env python3
import sqlite3
import argparse
from datetime import datetime
import json
import os
import sys

DB_PATH = 'gtfs_metro.db'

def get_db_connection():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found. Please run build_db.py first.", file=sys.stderr)
        sys.exit(1)
    return sqlite3.connect(DB_PATH)

def get_available_dates(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM calendar_dates ORDER BY date ASC")
    dates = [row[0] for row in cursor.fetchall()]
    return dates

def get_closest_date(target_date_str, available_dates):
    if not available_dates:
        return None
    
    # Try to find target_date_str exactly
    if target_date_str in available_dates:
        return target_date_str
        
    # Convert to datetime objects and find the closest
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

def query_station_schedule(conn, station_query, date_str):
    cursor = conn.cursor()
    
    # 1. Match stops by station name substring
    cursor.execute("""
        SELECT stop_id, stop_name, stop_lat, stop_lon 
        FROM stops 
        WHERE stop_name LIKE ?
    """, (f'%{station_query}%',))
    matched_stops = cursor.fetchall()
    
    if not matched_stops:
        return {"error": f"No station matching '{station_query}' found."}
        
    stop_ids = [s[0] for s in matched_stops]
    stop_map = {s[0]: s[1] for s in matched_stops}
    
    # 2. Find active service IDs for the selected date
    cursor.execute("""
        SELECT service_id 
        FROM calendar_dates 
        WHERE date = ? AND exception_type = 1
    """, (date_str,))
    active_services = [row[0] for row in cursor.fetchall()]
    
    if not active_services:
        return {
            "station": station_query,
            "date": date_str,
            "stops": [s[1] for s in matched_stops],
            "departures": [],
            "message": f"No active service schedule found for date {date_str}."
        }
        
    # 3. Query stop times joined with routes and trips
    # We dynamically construct placeholders for SQL IN clauses
    stop_placeholders = ','.join('?' for _ in stop_ids)
    service_placeholders = ','.join('?' for _ in active_services)
    
    query = f"""
        SELECT 
            r.route_id,
            r.route_short_name,
            r.route_long_name,
            r.route_color,
            st.stop_id,
            st.departure_time,
            t.trip_headsign,
            t.direction_id
        FROM stop_times st
        JOIN trips t ON st.trip_id = t.trip_id
        JOIN routes r ON t.route_id = r.route_id
        WHERE st.stop_id IN ({stop_placeholders})
          AND t.service_id IN ({service_placeholders})
        ORDER BY st.departure_time ASC
    """
    
    params = stop_ids + active_services
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    departures = []
    for row in rows:
        departures.append({
            "line": row[0],
            "line_name": row[2],
            "color": f"#{row[3]}" if row[3] else "#999999",
            "station_platform": stop_map[row[4]],
            "departure_time": row[5],
            "destination": row[6].upper() if row[6] else "UNKNOWN",
            "direction_id": row[7]
        })
        
    return {
        "station": station_query,
        "queried_date": date_str,
        "stops": [s[1] for s in matched_stops],
        "departures": departures
    }

def print_text_table(result):
    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        return
        
    print("=" * 80)
    print(f"TIMETABLE FOR STATION QUERY: '{result['station'].upper()}'")
    print(f"Date: {result['queried_date']} | Stops matched: {', '.join(result['stops'])}")
    print("=" * 80)
    
    deps = result["departures"]
    if not deps:
        print("No departures found.")
        return
        
    # Headers
    print(f"{'TIME':<8} | {'LINE':<4} | {'PLATFORM':<25} | {'DESTINATION':<25} | {'DIR'}")
    print("-" * 80)
    
    for d in deps:
        print(f"{d['departure_time']:<8} | {d['line']:<4} | {d['station_platform']:<25} | {d['destination']:<25} | {d['direction_id']}")
        
    print("-" * 80)
    print(f"Total departures: {len(deps)}")
    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Query ATM Milano Metro schedules.")
    parser.add_argument("--station", required=True, help="Sub-string of the station name (e.g. Duomo, Cadorna)")
    parser.add_argument("--date", help="Date to query in YYYY-MM-DD or YYYYMMDD format (defaults to today)")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    
    args = parser.parse_args()
    
    conn = get_db_connection()
    
    # Resolve target date
    if args.date:
        # Clean formatting separators
        raw_date = args.date.replace('-', '').replace('/', '')
    else:
        raw_date = datetime.now().strftime("%Y%m%d")
        
    available_dates = get_available_dates(conn)
    
    # Get closest active date
    resolved_date = get_closest_date(raw_date, available_dates)
    
    if not resolved_date:
        print("Error: No dates found in the database. Please rebuild database.", file=sys.stderr)
        sys.exit(1)
        
    if resolved_date != raw_date:
        if not args.json:
            print(f"Warning: Queried date '{raw_date}' is outside feed limits. Falling back to closest active date '{resolved_date}'")
            
    # Run the query
    result = query_station_schedule(conn, args.station, resolved_date)
    conn.close()
    
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_text_table(result)

if __name__ == '__main__':
    main()
