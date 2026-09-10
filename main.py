import io
import os
import zipfile
import json
import xml.etree.ElementTree as ET
import pyproj
import simplekml
import openpyxl
import requests
import numpy as np
import time
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from shapely.geometry import Polygon, Point, shape
from shapely.ops import transform
from flask import Flask, request, jsonify, send_file, send_from_directory

app = Flask(__name__)

# ==========================================
# 🗺️ ระบบค้นหาสถานที่ (Local Reverse Geocoding)
# ==========================================
tambon_features = []
geojson_path = 'tambon_thailand.json'

if os.path.exists(geojson_path):
    print("กำลังโหลดข้อมูลขอบเขตตำบลเพื่อใช้ค้นหาสถานที่ (ทำงานเบื้องหลัง)...")
    try:
        with open(geojson_path, 'r', encoding='utf-8') as f:
            gj_data = json.load(f)
            for feat in gj_data.get('features', []):
                if feat.get('geometry'):
                    geom = shape(feat['geometry'])
                    tambon_features.append({
                        'geom': geom,
                        'bounds': geom.bounds,
                        'props': feat.get('properties', {})
                    })
        print(f"โหลดข้อมูลตำบลสำเร็จ {len(tambon_features)} รายการ พร้อมใช้งาน!")
    except Exception as e:
        print(f"⚠️ ไม่สามารถโหลดไฟล์ {geojson_path} ได้: {e}")
else:
    print(f"⚠️ ไม่พบไฟล์ {geojson_path} ระบบจะไม่สามารถดึงชื่อสถานที่อัตโนมัติได้")

def get_local_location(lon, lat):
    """ฟังก์ชันเช็ค Point in Polygon แบบเร็ว"""
    if not tambon_features:
        return ""
    
    pt = Point(lon, lat)
    for item in tambon_features:
        minx, miny, maxx, maxy = item['bounds']
        if minx <= lon <= maxx and miny <= lat <= maxy:
            if item['geom'].contains(pt):
                props = item['props']
                tam = props.get('TAM_NAM_T', '')
                amp = props.get('AMPHOE_T', '')
                prov = props.get('PROV_NAM_T', '')
                
                parts = []
                if tam: parts.append(tam)
                if amp: parts.append(amp)
                if prov: parts.append(prov)
                
                return " ".join(parts).strip()
    return ""

def dd_to_dms(dd, is_lat=True):
    direction = ("N" if dd >= 0 else "S") if is_lat else ("E" if dd >= 0 else "W")
    dd = abs(dd)
    degrees = int(dd)
    minutes_float = (dd - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 2)
    return f'{degrees}° {minutes}\' {seconds:.2f}" {direction}'


# ==========================================
# ⛰️ ฟังก์ชันหาความสูงภูมิประเทศ (Elevation - High Precision + Multi-Provider Fallback)
# ==========================================
def fetch_elevation_safe(lats, lons):
    """ระบบดึงความสูงอัจฉริยะ: มีระบบ Retry (ลองซ้ำ) และ Multi-API Fallback สำรอง"""
    if not lats or not lons:
        return []
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    # -------------------------------------------------------------
    # 1. ตัวเลือกหลัก: Open-Meteo API (พร้อมระบบ Retry 3 รอบ)
    # -------------------------------------------------------------
    url_open_meteo = f"https://api.open-meteo.com/v1/elevation?latitude={','.join(map(str, lats))}&longitude={','.join(map(str, lons))}"
    
    for attempt in range(3):
        try:
            response = requests.get(url_open_meteo, headers=headers, timeout=8)
            if response.status_code == 200:
                data = response.json().get("elevation", [])
                if data and any(e is not None for e in data):
                    return [e if e is not None else 10.0 for e in data]
            elif response.status_code == 429:
                sleep_time = 1.0 * (attempt + 1)
                print(f"⚠️ Open-Meteo Rate Limited (429), retrying in {sleep_time}s...")
                time.sleep(sleep_time)
        except Exception as e:
            print(f"Open-Meteo attempt {attempt+1} error: {e}")
        time.sleep(0.3)

    # -------------------------------------------------------------
    # 2. ตัวสำรอง (Fallback API): Open-Elevation API
    # -------------------------------------------------------------
    try:
        print("⚠️ Open-Meteo ขัดข้อง กำลังสลับไปใช้ Open-Elevation API สำรอง...")
        locations_str = "|".join([f"{lat},{lon}" for lat, lon in zip(lats, lons)])
        url_fallback = f"https://api.open-elevation.com/api/v1/lookup?locations={locations_str}"
        
        response = requests.get(url_fallback, headers=headers, timeout=10)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                elevs = [res.get("elevation") for res in results]
                if any(e is not None for e in elevs):
                    return [e if e is not None else 10.0 for e in elevs]
    except Exception as e:
        print(f"⚠️ Fallback API Error: {e}")

    # -------------------------------------------------------------
    # 3. เซฟตี้สุดท้าย: หากล่มทั้งหมด ป้องกันเว็บพังด้วยค่าสำรองปลอดภัย
    # -------------------------------------------------------------
    print("❌ API ทั้งหมดไม่ตอบสนอง ใช้ระบบค่าความสูงสำรองฉุกเฉิน")
    return [30.0] * len(lats)

def get_elevation_data(coords):
    """ดึงข้อมูลความสูงแบบเพิ่มความหนาแน่น (High-Density Grid & Boundary Sampling) แม่นยำสูง"""
    try:
        poly = Polygon(coords)
        if not poly.is_valid:
            poly = poly.buffer(0)
            
        minx, miny, maxx, maxy = poly.bounds
        
        span_x = maxx - minx
        span_y = maxy - miny
        step_x = span_x / 20.0
        step_y = span_y / 20.0
        if step_x == 0: step_x = 0.005
        if step_y == 0: step_y = 0.005

        lons = np.arange(minx, maxx + step_x/2, step_x)
        lats = np.arange(miny, maxy + step_y/2, step_y)
        
        grid_lats = []
        grid_lons = []
        
        for lat in lats:
            for lon in lons:
                pt = Point(lon, lat)
                if poly.contains(pt) or poly.touches(pt):
                    grid_lats.append(round(lat, 5))
                    grid_lons.append(round(lon, 5))
        
        for i in range(len(coords)):
            p1 = coords[i]
            p2 = coords[(i + 1) % len(coords)]
            grid_lats.append(round(p1[1], 5))
            grid_lons.append(round(p1[0], 5))
            
            mid_lon = (p1[0] + p2[0]) / 2.0
            mid_lat = (p1[1] + p2[1]) / 2.0
            grid_lats.append(round(mid_lat, 5))
            grid_lons.append(round(mid_lon, 5))
            
        seen = set()
        unique_lats = []
        unique_lons = []
        for lat, lon in zip(grid_lats, grid_lons):
            if (lat, lon) not in seen:
                seen.add((lat, lon))
                unique_lats.append(lat)
                unique_lons.append(lon)

        max_chunk = 80
        all_elevations = []
        
        for i in range(0, len(unique_lats), max_chunk):
            chunk_lats = unique_lats[i:i + max_chunk]
            chunk_lons = unique_lons[i:i + max_chunk]
            elevs = fetch_elevation_safe(chunk_lats, chunk_lons)
            if elevs:
                all_elevations.extend([e for e in elevs if e is not None])
            time.sleep(0.2)
        
        if all_elevations:
            return {
                "min_elevation": round(min(all_elevations), 1),
                "max_elevation": round(max(all_elevations), 1)
            }
    except Exception as e:
        print(f"Elevation Processing Error: {e}")
        
    return {"min_elevation": 10.0, "max_elevation": 50.0}

@app.route('/')
def index():
    if os.path.exists('index.html'):
        return send_from_directory('.', 'index.html')
    elif os.path.exists('templates_index.html'):
        return send_from_directory('.', 'templates_index.html')
    return "<h3>⚠️ หาไฟล์ HTML ไม่เจอ</h3>", 404

@app.route('/<path:filename>')
def serve_static(filename):
    if os.path.exists(filename):
        return send_from_directory('.', filename)
    return jsonify({"error": "File not found"}), 404

# ==========================================
# ✈️ AIRPLANE API
# ==========================================
@app.route('/api/calculate', methods=['POST'])
def calculate_area():
    data = request.json or {}
    buf_nw = data.get('buffer_nw', [0, 0])
    buf_ne = data.get('buffer_ne', [0, 0])
    buf_se = data.get('buffer_se', [0, 0])
    buf_sw = data.get('buffer_sw', [0, 0])

    corners = {
        "NW": {"lat": buf_nw[0], "lng": buf_nw[1], "lat_dms": dd_to_dms(buf_nw[0], True), "lng_dms": dd_to_dms(buf_nw[1], False)},
        "NE": {"lat": buf_ne[0], "lng": buf_ne[1], "lat_dms": dd_to_dms(buf_ne[0], True), "lng_dms": dd_to_dms(buf_ne[1], False)},
        "SE": {"lat": buf_se[0], "lng": buf_se[1], "lat_dms": dd_to_dms(buf_se[0], True), "lng_dms": dd_to_dms(buf_se[1], False)},
        "SW": {"lat": buf_sw[0], "lng": buf_sw[1], "lat_dms": dd_to_dms(buf_sw[0], True), "lng_dms": dd_to_dms(buf_sw[1], False)}
    }
    return jsonify({"status": "success", "corners": corners})

@app.route('/api/download', methods=['POST'])
def download_package():
    data = request.json or {}
    project_name = data.get('project_name', 'NOTAM_PROJECT').strip().replace(' ', '_')
    sheets = data.get('sheets', [])

    in_nw = data.get('inner_nw', [0, 0])
    in_ne = data.get('inner_ne', [0, 0])
    in_se = data.get('inner_se', [0, 0])
    in_sw = data.get('inner_sw', [0, 0])

    buf_nw = data.get('buffer_nw', [0, 0])
    buf_ne = data.get('buffer_ne', [0, 0])
    buf_se = data.get('buffer_se', [0, 0])
    buf_sw = data.get('buffer_sw', [0, 0])

    merged_sheet_name = "_".join([s.replace(" ", "") for s in sheets]) if sheets else "Merged_Block"

    kml_block = simplekml.Kml()
    pol_block = kml_block.newpolygon(name=merged_sheet_name)
    pol_block.outerboundaryis = [(in_sw[1], in_sw[0]), (in_se[1], in_se[0]), (in_ne[1], in_ne[0]), (in_nw[1], in_nw[0]), (in_sw[1], in_sw[0])]
    pol_block.style.polystyle.color = '4000ffff'
    pol_block.style.linestyle.color = 'ff00ffff'
    pol_block.style.linestyle.width = 3

    kml_notam = simplekml.Kml()
    pol_notam = kml_notam.newpolygon(name=f"{project_name}_Buffer")
    pol_notam.outerboundaryis = [(buf_sw[1], buf_sw[0]), (buf_se[1], buf_se[0]), (buf_ne[1], buf_ne[0]), (buf_nw[1], buf_nw[0]), (buf_sw[1], buf_sw[0])]
    pol_notam.style.polystyle.color = '400000ff'
    pol_notam.style.linestyle.color = 'ff0000ff'
    pol_notam.style.linestyle.width = 2

    for name, p in [("NW", buf_nw), ("NE", buf_ne), ("SE", buf_se), ("SW", buf_sw)]:
        pnt = kml_notam.newpoint(name=name, coords=[(p[1], p[0])])
        pnt.description = f"Lat: {p[0]:.6f}, Lon: {p[1]:.6f}\nDMS: {dd_to_dms(p[0], True)}, {dd_to_dms(p[1], False)}"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Coordinates"
    ws.views.sheetView[0].showGridLines = True
    ws.append(["Corner Position", "Latitude (DMS)", "Longitude (DMS)", "Latitude (DD)", "Longitude (DD)"])

    rows = [
        ["NW (บน-ซ้าย)", dd_to_dms(buf_nw[0], True), dd_to_dms(buf_nw[1], False), round(buf_nw[0], 6), round(buf_nw[1], 6)],
        ["NE (บน-ขวา)", dd_to_dms(buf_ne[0], True), dd_to_dms(buf_ne[1], False), round(buf_ne[0], 6), round(buf_ne[1], 6)],
        ["SE (ล่าง-ขวา)", dd_to_dms(buf_se[0], True), dd_to_dms(buf_se[1], False), round(buf_se[0], 6), round(buf_se[1], 6)],
        ["SW (ล่าง-ซ้าย)", dd_to_dms(buf_sw[0], True), dd_to_dms(buf_sw[1], False), round(buf_sw[0], 6), round(buf_sw[1], 6)]
    ]
    for r in rows: ws.append(r)

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin_border = Border(left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'), top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9'))

    for col in range(1, 6):
        c = ws.cell(row=1, column=col)
        c.fill = header_fill; c.font = header_font; c.alignment = Alignment(horizontal="center")

    for r in range(2, 6):
        for c in range(1, 6):
            cell = ws.cell(row=r, column=c)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center")

    for col, width in {'A': 18, 'B': 22, 'C': 22, 'D': 18, 'E': 18}.items():
        ws.column_dimensions[col].width = width

    excel_bytes = io.BytesIO()
    wb.save(excel_bytes)
    excel_bytes.seek(0)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{merged_sheet_name}.kml", kml_block.kml().encode('utf-8'))
        z.writestr(f"{project_name}_Buffer.kml", kml_notam.kml().encode('utf-8'))
        z.writestr(f"{project_name}_coordinates.xlsx", excel_bytes.getvalue())

    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name=f'{project_name}_Package.zip')


# ==========================================
# 🚁 UAV API (รองรับ Smart Caching)
# ==========================================
@app.route('/api/calculate_uav', methods=['POST'])
def calculate_uav():
    data = request.get_json() or {}
    coords = data.get('coordinates', [])
    buffer_meters = float(data.get('buffer_meters', 50))
    
    cached_min = data.get('cached_min_elevation')
    cached_max = data.get('cached_max_elevation')
    
    if len(coords) < 3:
        return jsonify({"error": "Need at least 3 coordinates"}), 400
    
    poly = Polygon(coords)
    centroid_lon = poly.centroid.x
    centroid_lat = poly.centroid.y
    
    utm_zone = int((centroid_lon + 180) / 6) + 1
    epsg_code = f"326{utm_zone}" if centroid_lat >= 0 else f"327{utm_zone}"
    
    project_to_utm = pyproj.Transformer.from_crs('EPSG:4326', f'EPSG:{epsg_code}', always_xy=True).transform
    project_to_wgs84 = pyproj.Transformer.from_crs(f'EPSG:{epsg_code}', 'EPSG:4326', always_xy=True).transform
    
    poly_utm = transform(project_to_utm, poly)
    buffer_utm = poly_utm.buffer(buffer_meters, join_style=2)
    
    poly_wgs84 = transform(project_to_wgs84, poly_utm)
    buffer_wgs84 = transform(project_to_wgs84, buffer_utm)

    location_name = get_local_location(centroid_lon, centroid_lat)
    
    if cached_min is not None and cached_max is not None:
        elevation_data = {
            "min_elevation": cached_min,
            "max_elevation": cached_max
        }
    else:
        elevation_data = get_elevation_data(list(poly_wgs84.exterior.coords))
    
    return jsonify({
        "inner_coords": list(poly_wgs84.exterior.coords),
        "buffer_coords": list(buffer_wgs84.exterior.coords),
        "location_name": location_name,
        "elevation": elevation_data,
        "centroid": [centroid_lon, centroid_lat]
    })

@app.route('/api/upload_kml', methods=['POST'])
def upload_kml():
    if 'file' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    filename = file.filename.lower()
    content = file.read()
    coords = []
    
    try:
        if filename.endswith('.geojson'):
            data = json.loads(content)
            for feat in data.get('features', []):
                geom = feat.get('geometry', {})
                if geom.get('type') == 'Polygon':
                    coords = geom.get('coordinates', [[]])[0]
                    break
                elif geom.get('type') == 'MultiPolygon':
                    coords = geom.get('coordinates', [[[]]])[0][0]
                    break
        elif filename.endswith('.kml'):
            root = ET.fromstring(content)
            for elem in root.iter():
                if elem.tag.endswith('coordinates'):
                    text = elem.text.strip()
                    pts = []
                    for part in text.split():
                        subparts = part.split(',')
                        if len(subparts) >= 2:
                            pts.append([float(subparts[0]), float(subparts[1])])
                    if len(pts) >= 3:
                        coords = pts
                        break
    except Exception as e:
        return jsonify({"error": f"Parsing error: {str(e)}"}), 400
        
    if not coords:
        return jsonify({"error": "Could not extract polygon coordinates from file"}), 400
        
    return jsonify({"status": "success", "coordinates": coords})


# ==========================================
# 📡 UAV LINE-OF-SIGHT (LOS) ANALYSIS API
# ==========================================
@app.route('/api/analyze_uav_los', methods=['POST'], strict_slashes=False)
def analyze_uav_los():
    """คำนวณโปรไฟล์ความสูงแนวบินและตรวจสอบจุดบดบังสัญญาณ (LOS) แบบเสถียร"""
    data = request.get_json() or {}
    base_coords = data.get('base_coords')      
    target_coords = data.get('target_coords')  
    antenna_height = float(data.get('antenna_height', 5.0))  
    drone_alt_agl = float(data.get('drone_alt_agl', 100.0))  

    if not base_coords or not target_coords:
        return jsonify({"error": "Missing base_coords or target_coords"}), 400

    lon1, lat1 = base_coords[0], base_coords[1]
    lon2, lat2 = target_coords[0], target_coords[1]

    geod = pyproj.Geod(ellps='WGS84')
    _, _, total_distance = geod.inv(lon1, lat1, lon2, lat2)

    num_samples = 25
    sampled_coords = [[lon1, lat1]]
    if num_samples > 2:
        npts = geod.npts(lon1, lat1, lon2, lat2, num_samples - 2)
        for pt in npts:
            sampled_coords.append([pt[0], pt[1]])
    sampled_coords.append([lon2, lat2])

    lats = [str(c[1]) for c in sampled_coords]
    lons = [str(c[0]) for c in sampled_coords]
    
    elevations = fetch_elevation_safe(lats, lons)

    if len(elevations) != len(sampled_coords):
        elevations = [10.0] * len(sampled_coords)

    z_start_msl = elevations[0] + antenna_height
    z_end_msl = elevations[-1] + drone_alt_agl

    profile = []
    is_blocked = False
    obstructions = []

    for i, coord in enumerate(sampled_coords):
        if i == 0:
            dist_from_base = 0.0
        elif i == len(sampled_coords) - 1:
            dist_from_base = total_distance
        else:
            _, _, dist_from_base = geod.inv(lon1, lat1, coord[0], coord[1])

        ratio = dist_from_base / total_distance if total_distance > 0 else 0
        los_alt_msl = z_start_msl + ratio * (z_end_msl - z_start_msl)
        terrain_alt_msl = elevations[i]

        blocked = terrain_alt_msl > los_alt_msl
        if blocked:
            is_blocked = True
            obstructions.append({
                "distance_m": round(dist_from_base, 1),
                "terrain_m": round(terrain_alt_msl, 1),
                "los_m": round(los_alt_msl, 1),
                "coord": coord
            })

        profile.append({
            "distance_m": round(dist_from_base, 1),
            "terrain_m": round(terrain_alt_msl, 1),
            "los_m": round(los_alt_msl, 1),
            "blocked": blocked,
            "coord": coord
        })

    base_location = get_local_location(lon1, lat1)

    return jsonify({
        "status": "success",
        "is_blocked": is_blocked,
        "total_distance_m": round(total_distance, 1),
        "base_info": {
            "coord": base_coords,
            "antenna_height_m": antenna_height,
            "terrain_elevation_m": round(elevations[0], 1),
            "total_antenna_msl": round(z_start_msl, 1),
            "location_name": base_location
        },
        "target_info": {
            "coord": target_coords,
            "drone_alt_agl_m": drone_alt_agl,
            "terrain_elevation_m": round(elevations[-1], 1),
            "total_drone_msl": round(z_end_msl, 1)
        },
        "profile": profile,
        "obstructions_count": len(obstructions)
    })

# ==========================================
# 📥 DOWNLOAD UAV PACKAGE & EXCEL
# ==========================================
@app.route('/api/download_uav', methods=['POST'])
def download_uav():
    data = request.get_json() or {}
    project_name = data.get('project_name', 'UAV_PROJECT').strip().replace(' ', '_')
    location_name = data.get('location_name', '-') 
    coords = data.get('coordinates', [])
    buffer_meters = float(data.get('buffer_meters', 50))
    
    base_point = data.get('base_point')
    
    min_elev = data.get('min_elevation')
    max_elev = data.get('max_elevation')
    if min_elev is None or max_elev is None:
        elev_data = get_elevation_data(coords)
        min_elev = elev_data.get('min_elevation', 10.0)
        max_elev = elev_data.get('max_elevation', 50.0)
    
    if len(coords) < 3:
        return jsonify({"error": "Invalid coordinates"}), 400
        
    poly = Polygon(coords)
    centroid_lon = poly.centroid.x
    centroid_lat = poly.centroid.y
    
    utm_zone = int((centroid_lon + 180) / 6) + 1
    epsg_code = f"326{utm_zone}" if poly.centroid.y >= 0 else f"327{utm_zone}"
    
    project_to_utm = pyproj.Transformer.from_crs('EPSG:4326', f'EPSG:{epsg_code}', always_xy=True).transform
    project_to_wgs84 = pyproj.Transformer.from_crs(f'EPSG:{epsg_code}', 'EPSG:4326', always_xy=True).transform
    
    poly_utm = transform(project_to_utm, poly)
    buffer_utm = poly_utm.buffer(buffer_meters, join_style=2)
    
    poly_wgs84 = transform(project_to_wgs84, poly_utm)
    buffer_wgs84 = transform(project_to_wgs84, buffer_utm)
    
    kml_inner = simplekml.Kml()
    pol_inner = kml_inner.newpolygon(name=f"{project_name}_Mission_Area")
    pol_inner.outerboundaryis = [(c[0], c[1]) for c in poly_wgs84.exterior.coords]
    pol_inner.style.polystyle.color = '4000a5ff'
    pol_inner.style.linestyle.color = 'ff00a5ff'
    pol_inner.style.linestyle.width = 3

    kml_buffer = simplekml.Kml()
    pol_buf = kml_buffer.newpolygon(name=f"{project_name}_Buffer_{buffer_meters}m")
    buffer_coords = list(buffer_wgs84.exterior.coords)
    pol_buf.outerboundaryis = [(c[0], c[1]) for c in buffer_coords]
    pol_buf.style.polystyle.color = '400000ef'
    pol_buf.style.linestyle.color = 'ff0000ef'
    pol_buf.style.linestyle.width = 2

    for idx, pt in enumerate(buffer_coords[:-1], start=1):
        lon, lat = pt[0], pt[1]
        pnt = kml_buffer.newpoint(name=f"Buf_Pt_{idx}", coords=[(lon, lat)])
        pnt.description = f"Lat: {lat:.6f}, Lon: {lon:.6f}\nDMS: {dd_to_dms(lat, True)}, {dd_to_dms(lon, False)}"

    def safe_round(val, places=6):
        try:
            return round(float(val), places)
        except (ValueError, TypeError):
            return str(val)

    if base_point:
        tk_lat_dd = base_point.get('lat', '-')
        tk_lng_dd = base_point.get('lng', '-')
        
        if tk_lat_dd != '-' and tk_lng_dd != '-':
            tk_lat_dms = dd_to_dms(tk_lat_dd, True)
            tk_lng_dms = dd_to_dms(tk_lng_dd, False)
        else:
            tk_lat_dms, tk_lng_dms = "-", "-"
    else:
        tk_lat_dms, tk_lng_dms, tk_lat_dd, tk_lng_dd = "-", "-", "-", "-"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mission_Data"
    ws.views.sheetView[0].showGridLines = False 
    
    ws.append(["📌 Project Name:", project_name])
    ws.append(["📍 Location:", location_name])
    
    ws.append(["🚀 Take-off Latitude:", tk_lat_dms, safe_round(tk_lat_dd)])
    ws.append(["🚀 Take-off Longitude:", tk_lng_dms, safe_round(tk_lng_dd)])
    
    ws.append(["🎯 Centroid Latitude:", dd_to_dms(centroid_lat, True), round(centroid_lat, 6)])
    ws.append(["🎯 Centroid Longitude:", dd_to_dms(centroid_lon, False), round(centroid_lon, 6)])
    ws.append(["📏 Buffer Distance:", f"{buffer_meters} Meters"])
    ws.append(["⛰️ Min Elevation (SRTM):", f"{min_elev} Meters MSL"])
    ws.append(["⛰️ Max Elevation (SRTM):", f"{max_elev} Meters MSL"])
    ws.append([]) 

    bold_font = Font(bold=True)
    for r in range(1, 10):
        ws.cell(row=r, column=1).font = bold_font

    table_start_row = 11
    ws.append(["Buffer Vertex", "Latitude (DMS)", "Longitude (DMS)", "Latitude (DD)", "Longitude (DD)"])

    for idx, pt in enumerate(buffer_coords[:-1], start=1):
        lon, lat = pt[0], pt[1]
        ws.append([f"Point {idx}", dd_to_dms(lat, True), dd_to_dms(lon, False), round(lat, 6), round(lon, 6)])

    header_fill = PatternFill(start_color="D96B27", end_color="D96B27", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin_border = Border(left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'), top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9'))

    for col in range(1, 6):
        c = ws.cell(row=table_start_row, column=col)
        c.fill = header_fill; c.font = header_font; c.alignment = Alignment(horizontal="center")

    for r in range(table_start_row + 1, table_start_row + len(buffer_coords)):
        for c in range(1, 6):
            cell = ws.cell(row=r, column=c)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center")

    for col, width in {'A': 22, 'B': 22, 'C': 22, 'D': 18, 'E': 18}.items():
        ws.column_dimensions[col].width = width

    excel_bytes = io.BytesIO()
    wb.save(excel_bytes)
    excel_bytes.seek(0)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{project_name}_Mission_Area.kml", kml_inner.kml().encode('utf-8'))
        z.writestr(f"{project_name}_Buffer_{buffer_meters}m.kml", kml_buffer.kml().encode('utf-8'))
        z.writestr(f"{project_name}_coordinates.xlsx", excel_bytes.getvalue())

    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', as_attachment=True, download_name=f'{project_name}_UAV_Package.zip')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
