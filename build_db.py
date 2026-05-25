#!/usr/bin/env python3
import csv
import sqlite3
import os
import time

DB_PATH = 'gtfs_metro.db'
GTFS_DIR = 'gtfs'

# Metro routes we want to extract
METRO_ROUTE_IDS = {'M1', 'M2', 'M3', 'M4', 'M5'}

def main():
    start_time = time.time()
    print("Starting GTFS metro extraction and SQLite compilation...")
    
    # 1. Connect to SQLite database
    if os.path.exists(DB_PATH):
        print(f"Removing existing database at {DB_PATH}")
        os.remove(DB_PATH)
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 2. Create tables
    print("Creating database schema...")
    cursor.execute("""
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY,
            route_short_name TEXT,
            route_long_name TEXT,
            route_color TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY,
            stop_name TEXT,
            stop_lat REAL,
            stop_lon REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY,
            route_id TEXT,
            service_id TEXT,
            trip_headsign TEXT,
            direction_id INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE stop_times (
            trip_id TEXT,
            arrival_time TEXT,
            departure_time TEXT,
            stop_id TEXT,
            stop_sequence INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE calendar_dates (
            service_id TEXT,
            date TEXT,
            exception_type INTEGER
        )
    """)
    conn.commit()
    
    # Keep track of filtered elements in memory to build other tables
    metro_trip_ids = set()
    metro_service_ids = set()
    metro_stop_ids = set()
    
    # 3. Parse and insert routes.txt
    print("Processing routes.txt...")
    routes_path = os.path.join(GTFS_DIR, 'routes.txt')
    routes_to_insert = []
    with open(routes_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rid = row['route_id']
            if rid in METRO_ROUTE_IDS:
                routes_to_insert.append((
                    rid,
                    row.get('route_short_name', ''),
                    row.get('route_long_name', ''),
                    row.get('route_color', '')
                ))
    cursor.executemany("INSERT INTO routes VALUES (?, ?, ?, ?)", routes_to_insert)
    conn.commit()
    print(f"Inserted {len(routes_to_insert)} metro routes.")
    
    # 4. Parse and insert trips.txt
    print("Processing trips.txt...")
    trips_path = os.path.join(GTFS_DIR, 'trips.txt')
    trips_to_insert = []
    with open(trips_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rid = row['route_id']
            if rid in METRO_ROUTE_IDS:
                tid = row['trip_id']
                sid = row['service_id']
                metro_trip_ids.add(tid)
                metro_service_ids.add(sid)
                trips_to_insert.append((
                    tid,
                    rid,
                    sid,
                    row.get('trip_headsign', ''),
                    int(row['direction_id']) if row.get('direction_id') else 0
                ))
    cursor.executemany("INSERT INTO trips VALUES (?, ?, ?, ?, ?)", trips_to_insert)
    conn.commit()
    print(f"Inserted {len(trips_to_insert)} metro trips.")
    
    # 5. Parse and insert calendar_dates.txt
    print("Processing calendar_dates.txt...")
    cal_dates_path = os.path.join(GTFS_DIR, 'calendar_dates.txt')
    cal_dates_to_insert = []
    with open(cal_dates_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row['service_id']
            if sid in metro_service_ids:
                cal_dates_to_insert.append((
                    sid,
                    row['date'],
                    int(row['exception_type'])
                ))
    cursor.executemany("INSERT INTO calendar_dates VALUES (?, ?, ?)", cal_dates_to_insert)
    conn.commit()
    print(f"Inserted {len(cal_dates_to_insert)} calendar date exceptions.")
    
    # 6. Parse and insert stop_times.txt (Streaming in batches)
    print("Processing stop_times.txt (streaming)...")
    stop_times_path = os.path.join(GTFS_DIR, 'stop_times.txt')
    batch_size = 50000
    stop_times_batch = []
    total_stop_times = 0
    
    # We use lower-level csv.reader for maximum parsing speed
    with open(stop_times_path, 'r', encoding='utf-8') as f:
        # Determine headers first
        header_line = f.readline().strip()
        headers = [h.replace('"', '').strip() for h in header_line.split(',')]
        
        # Map field names to indices
        trip_idx = headers.index('trip_id')
        arr_idx = headers.index('arrival_time')
        dep_idx = headers.index('departure_time')
        stop_idx = headers.index('stop_id')
        seq_idx = headers.index('stop_sequence')
        
        reader = csv.reader(f)
        for line_no, row in enumerate(reader, start=2):
            if not row:
                continue
            tid = row[trip_idx]
            if tid in metro_trip_ids:
                sid = row[stop_idx]
                metro_stop_ids.add(sid)
                
                stop_times_batch.append((
                    tid,
                    row[arr_idx],
                    row[dep_idx],
                    sid,
                    int(row[seq_idx])
                ))
                
                if len(stop_times_batch) >= batch_size:
                    cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", stop_times_batch)
                    total_stop_times += len(stop_times_batch)
                    stop_times_batch = []
                    
            if line_no % 1000000 == 0:
                print(f"  Processed {line_no} lines...")
                
        # Insert any remaining records in the batch
        if stop_times_batch:
            cursor.executemany("INSERT INTO stop_times VALUES (?, ?, ?, ?, ?)", stop_times_batch)
            total_stop_times += len(stop_times_batch)
            
    conn.commit()
    print(f"Inserted {total_stop_times} stop times.")
    
    # 7. Parse and insert stops.txt
    print("Processing stops.txt...")
    stops_path = os.path.join(GTFS_DIR, 'stops.txt')
    stops_to_insert = []
    with open(stops_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row['stop_id']
            if sid in metro_stop_ids:
                stops_to_insert.append((
                    sid,
                    row['stop_name'],
                    float(row['stop_lat']) if row.get('stop_lat') else 0.0,
                    float(row['stop_lon']) if row.get('stop_lon') else 0.0
                ))
    cursor.executemany("INSERT INTO stops VALUES (?, ?, ?, ?)", stops_to_insert)
    conn.commit()
    print(f"Inserted {len(stops_to_insert)} distinct metro stops.")
    
    # 8. Create indexes
    print("Creating indexes for maximum query performance...")
    cursor.execute("CREATE INDEX idx_stop_times_stop ON stop_times(stop_id)")
    cursor.execute("CREATE INDEX idx_stop_times_trip ON stop_times(trip_id)")
    cursor.execute("CREATE INDEX idx_trips_route ON trips(route_id)")
    cursor.execute("CREATE INDEX idx_cal_dates_date ON calendar_dates(date)")
    cursor.execute("CREATE INDEX idx_cal_dates_service ON calendar_dates(service_id)")
    conn.commit()
    
    # 9. Clean up and finalize
    cursor.execute("VACUUM")
    conn.commit()
    conn.close()
    
    end_time = time.time()
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"Success! Database created at {DB_PATH} ({db_size:.2f} MB)")
    print(f"Total processing time: {end_time - start_time:.2f} seconds")

if __name__ == '__main__':
    main()
