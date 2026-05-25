#!/usr/bin/env python3
import http.server
import socketserver
import urllib.parse
import sqlite3
import json
import os
import sys
from datetime import datetime

PORT = 8080
DB_PATH = 'gtfs_metro.db'

def get_db_connection():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database file '{DB_PATH}' not found. Please run build_db.py first.", file=sys.stderr)
        sys.exit(1)
    return sqlite3.connect(DB_PATH)

def get_closest_date(conn, target_date_str):
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM calendar_dates ORDER BY date ASC")
    available_dates = [row[0] for row in cursor.fetchall()]
    
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

class MetroHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    
    def log_message(self, format, *args):
        # Silence default terminal logs for a cleaner output
        pass

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)
        
        # 1. Serve Frontend HTML
        if path == '/' or path == '/index.html':
            self.serve_static_file('index.html', 'text/html')
            return
            
        # 2. API: Get all stops grouped by metro line
        elif path == '/api/stops':
            self.handle_api_stops()
            return
            
        # 3. API: Get destinations for a stop and route
        elif path == '/api/destinations':
            self.handle_api_destinations(query_params)
            return
            
        # 4. API: Get next 3 departures (Schedule)
        elif path == '/api/schedule':
            self.handle_api_schedule(query_params)
            return
            
        # 5. Not Found
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b"Not Found")

    def serve_static_file(self, filename, content_type):
        if not os.path.exists(filename):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"File Not Found")
            return
            
        self.send_response(200)
        self.send_header('Content-type', f'{content_type}; charset=utf-8')
        self.end_headers()
        
        with open(filename, 'rb') as f:
            self.wfile.write(f.read())

    def handle_api_stops(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query distinct stop names with their corresponding line (route_id)
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
            
        conn.close()
        
        self.send_json_response(stops_by_line)

    def handle_api_destinations(self, params):
        stop_name = params.get('stop_name', [None])[0]
        route_id = params.get('route_id', [None])[0]
        
        if not stop_name or not route_id:
            self.send_json_response({"error": "Missing stop_name or route_id parameter"}, 400)
            return
            
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query unique trip headsigns (destinations) for this stop name and line
        cursor.execute("""
            SELECT DISTINCT t.trip_headsign
            FROM stop_times st
            JOIN trips t ON st.trip_id = t.trip_id
            JOIN stops s ON st.stop_id = s.stop_id
            WHERE s.stop_name = ? AND t.route_id = ?
            ORDER BY t.trip_headsign ASC
        """, (stop_name, route_id))
        
        destinations = []
        for row in cursor.fetchall():
            destinations.append({
                "trip_headsign": row[0]
            })
            
        conn.close()
        
        self.send_json_response(destinations)

    def handle_api_schedule(self, params):
        stop_name = params.get('stop_name', [None])[0]
        route_id = params.get('route_id', [None])[0]
        trip_headsigns = params.get('trip_headsign', [])
        query_time = params.get('time', [None])[0] # Format HH:MM:SS
        query_date = params.get('date', [None])[0] # Format YYYYMMDD
        
        if not stop_name or not route_id or not trip_headsigns or not query_time:
            self.send_json_response({"error": "Missing query parameters"}, 400)
            return
            
        conn = get_db_connection()
        
        # Get active dates and resolve to the closest one in feed calendar
        if query_date:
            today_str = query_date
        else:
            today_str = datetime.now().strftime("%Y%m%d")
            
        resolved_date = get_closest_date(conn, today_str)
        
        if not resolved_date:
            self.send_json_response({"error": "No calendar exceptions available in database"}, 500)
            conn.close()
            return
            
        cursor = conn.cursor()
        
        # Find active service IDs
        cursor.execute("""
            SELECT service_id 
            FROM calendar_dates 
            WHERE date = ? AND exception_type = 1
        """, (resolved_date,))
        active_services = [row[0] for row in cursor.fetchall()]
        
        if not active_services:
            self.send_json_response({
                "route_id": route_id,
                "departures": [],
                "message": f"No active schedule for date {resolved_date}"
            })
            conn.close()
            return
            
        # SQL query to get next 3 departures
        headsign_placeholders = ','.join('?' for _ in trip_headsigns)
        service_placeholders = ','.join('?' for _ in active_services)
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
            WHERE s.stop_name = ?
              AND t.route_id = ?
              AND t.trip_headsign IN ({headsign_placeholders})
              AND t.service_id IN ({service_placeholders})
              AND st.departure_time >= ?
            ORDER BY st.departure_time ASC
            LIMIT 3
        """
        
        params_list = [stop_name, route_id] + trip_headsigns + active_services + [query_time]
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
            
        conn.close()
        
        self.send_json_response({
            "route_id": route_id,
            "stop_name": stop_name,
            "resolved_date": resolved_date,
            "departures": departures
        })

    def send_json_response(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        # Allow cross-origin for local testing
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

def main():
    # Make sure we use the directory where server.py lives so relative imports work
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    Handler = MetroHTTPRequestHandler
    
    # Enable socket re-use to prevent 'Port already in use' errors on quick restarts
    socketserver.TCPServer.allow_reuse_address = True
    
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print("=" * 80)
        print("                 ORARIOGAIA METRO WEB APP ACTIVE! 🎉")
        print("-" * 80)
        print(f"  👉 Web Page URL:   http://localhost:{PORT}/")
        print("  👉 Stops API:      http://localhost:{PORT}/api/stops")
        print("-" * 80)
        print("  Press Ctrl+C to stop the server.")
        print("=" * 80)
        
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer shutting down... Bye!")
            sys.exit(0)

if __name__ == '__main__':
    main()
