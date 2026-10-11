/**
 * 🚫 NO-FLY ZONE & POLICE STATION FINDER MODULE (AeroFocus)
 * Offline Mode using Local GeoJSON Dataset + Multi-Station Filtering & Map Markers
 */
class NoFlyZoneManager {
    constructor(leafletMap) {
        this.map = leafletMap;
        this.layerGroup = L.layerGroup().addTo(this.map);
        this.policeMarkerGroup = L.layerGroup().addTo(this.map); // กลุ่ม Marker หมุดตำรวจบนแผนที่
        this.isLoaded = false;
        this.isActive = false;

        this.pdrData1 = null;
        this.pdrData2 = null;
        this.aeroData = null;
        this.policeData = null;

        this.createSidebarUI();
    }

    createSidebarUI() {
        if (document.getElementById('noflySidebar')) return;

        const sidebarHTML = `
            <div id="noflySidebar" class="fixed top-0 right-0 h-full w-80 sm:w-96 bg-[#1E222A] text-white z-[9999] shadow-2xl border-l border-slate-700 transition-transform duration-300 transform translate-x-full flex flex-col">
                <!-- Header -->
                <div class="p-4 bg-slate-800 border-b border-slate-700 flex justify-between items-center">
                    <div class="flex items-center gap-2">
                        <span class="text-red-500 font-bold text-lg">🚫</span>
                        <h3 class="font-bold text-sm text-white">Airspace Analysis</h3>
                    </div>
                    <button onclick="window.noFlyManager.closeSidebar()" class="text-slate-400 hover:text-white p-1 rounded hover:bg-slate-700">✕</button>
                </div>

                <!-- Content Zone -->
                <div id="noflySidebarContent" class="p-4 flex-1 overflow-y-auto text-xs space-y-4">
                    <p class="text-slate-400 italic">กำลังวิเคราะห์พื้นที่...</p>
                </div>
            </div>
        `;
        document.body.insertAdjacentHTML('beforeend', sidebarHTML);
    }

    async toggle() {
        this.isActive = !this.isActive;

        if (this.isActive) {
            if (!this.isLoaded) {
                await this.loadData();
            }
            this.map.addLayer(this.layerGroup);
            this.map.addLayer(this.policeMarkerGroup);
            this.openSidebar();
            await this.analyzeOverlap();
        } else {
            this.map.removeLayer(this.layerGroup);
            this.map.removeLayer(this.policeMarkerGroup);
            this.policeMarkerGroup.clearLayers();
            this.closeSidebar();
        }
    }

    async loadData(
        pdr1Path = './PDR-AREA1.json',
        pdr2Path = './PDR-AREA2.json',
        aeroPath = './Aerodrome.json',
        policePath = './police.json'
    ) {
        try {
            const [resPdr1, resPdr2, resAero, resPolice] = await Promise.all([
                fetch(pdr1Path).catch(() => null),
                fetch(pdr2Path).catch(() => null),
                fetch(aeroPath).catch(() => null),
                fetch(policePath).catch(() => null)
            ]);

            if (resPdr1?.ok) {
                this.pdrData1 = await resPdr1.json();
                this.layerGroup.addLayer(L.geoJSON(this.pdrData1, {
                    style: { color: '#FF0000', fillColor: '#FF0000', fillOpacity: 0.3, weight: 1.5 },
                    onEachFeature: (f, l) => l.bindPopup(`<b>🚫 เขตห้ามบิน (PDR 1):</b><br>${f.properties?.name || f.properties?.NAME || '-'}`)
                }));
            }

            if (resPdr2?.ok) {
                this.pdrData2 = await resPdr2.json();
                this.layerGroup.addLayer(L.geoJSON(this.pdrData2, {
                    style: { color: '#DC2626', fillColor: '#DC2626', fillOpacity: 0.3, weight: 1.5 },
                    onEachFeature: (f, l) => l.bindPopup(`<b>🚫 เขตห้ามบิน (PDR 2):</b><br>${f.properties?.name || f.properties?.NAME || '-'}`)
                }));
            }

            if (resAero?.ok) {
                this.aeroData = await resAero.json();
                this.layerGroup.addLayer(L.geoJSON(this.aeroData, {
                    style: { color: '#FF8800', fillColor: '#FFA500', fillOpacity: 0.2, weight: 1.2 },
                    onEachFeature: (f, l) => l.bindPopup(`<b>✈️ เขตรัศมีสนามบิน:</b><br>${f.properties?.name || f.properties?.NAME || '-'}`)
                }));
            }

            if (resPolice?.ok) {
                this.policeData = await resPolice.json();
                console.log("👮‍♂️ โหลดข้อมูล police.json เรียบร้อยแล้ว");
            } else {
                console.warn("⚠️ ไม่พบไฟล์ police.json ในโปรเจกต์");
            }

            this.isLoaded = true;
        } catch (e) {
            console.error('❌ Error loading spatial datasets:', e);
        }
    }

    // 🧹 ฟังก์ชันกรองเฉพาะ สภ./สน. พื้นที่ (Whitelist + Blacklist แบบเข้มงวด)
    isValidLocalPoliceStation(name) {
        if (!name) return false;
        const nameStr = String(name).trim();

        // 🟢 1. WHITELIST: ต้องมีคำว่า "สถานีตำรวจ" หรือ "สน." หรือ "สภ." เท่านั้น
        const isPoliceStation = nameStr.includes('สถานีตำรวจ') || 
                                nameStr.includes('สน.') || 
                                nameStr.includes('สภ.');

        if (!isPoliceStation) return false; // ถ้าไม่มีคำพวกนี้ ตัดทิ้งทันที!

        // 🔴 2. BLACKLIST: ถ้ามีคำพวกนี้ ให้ตัดทิ้ง (หน่วยงานบริหาร / กอง / ศูนย์)
        const blacklist = [
            'กองบัญชาการ', 'กบก.', 'กองบังคับการ', 'บก.', 'กองกำกับการ', 'กก.', 
            'กองคลัง', 'กองเกษตร', 'กองร้อย', 'กองงาน', 'ศูนย์', 'สถาบัน', 'มหาวิทยาลัย',
            'ทางหลวง', 'หน่วยปฏิบัติการพิเศษ', 'นปพ.', 'ศูนย์ฝึก', 'สืบสวน', 
            'พิสูจน์หลักฐาน', 'ตชด.', 'ตระเวนชายแดน', 'ท่องเที่ยว', 
            'ตรวจคนเข้าเมือง', 'ตม.', 'คอมมานโด', 'สายตรวจ', 'สอบสวนกลาง',
            'ภาค 1', 'ภาค 2', 'ภาค 3', 'ภาค 4', 'ภาค 5', 'ภาค 6', 'ภาค 7', 'ภาค 8', 'ภาค 9'
        ];

        for (let word of blacklist) {
            if (nameStr.includes(word)) return false;
        }

        return true;
    }

    // 👮‍♂️ ค้นหา สภ./สน. ใกล้เคียงทั้งหมด (เรียงตามระยะทาง คัดมาสูงสุดตาม maxResults)
    getNearbyPoliceStations(userPolyGeoJSON, maxResults = 3, maxDistanceKm = 25) {
        if (!userPolyGeoJSON || typeof turf === 'undefined' || !this.policeData) return [];

        try {
            const centerPt = turf.centerOfMass(userPolyGeoJSON);

            // 1. ดึงรายการ Features ทั้งหมด
            let rawFeatures = [];
            if (this.policeData.type === 'FeatureCollection' && Array.isArray(this.policeData.features)) {
                rawFeatures = this.policeData.features;
            } else if (Array.isArray(this.policeData)) {
                rawFeatures = this.policeData.map(item => ({
                    type: "Feature",
                    geometry: { type: "Point", coordinates: [item.lng || item.lon, item.lat] },
                    properties: item
                }));
            }

            if (rawFeatures.length === 0) return [];

            // 2. คำนวณระยะทางและคัดกรองเฉพาะ สภ./สน. ท้องที่
            const calculatedList = [];

            rawFeatures.forEach(feature => {
                const props = feature.properties || {};
                let name = props.name || props.NAME || props.Name || props.station_th || props.title || '';

                // เช็ก Whitelist & Blacklist
                if (!this.isValidLocalPoliceStation(name)) return;

                // ดึงพิกัด
                let coords = null;
                if (feature.geometry && feature.geometry.coordinates) {
                    coords = feature.geometry.coordinates; // [lng, lat]
                }

                if (!coords || !coords[0] || !coords[1]) return;

                const pt = turf.point(coords);
                const dist = turf.distance(centerPt, pt, { units: 'kilometers' });

                // กรองเฉพาะสถานีที่อยู่ในรัศมีกำหนด
                if (dist <= maxDistanceKm) {
                    let phone = props.phone || props.PHONE || props.tel || props.TEL || '191 (เบอร์กลาง)';
                    if (phone === '191 (เบอร์กลาง)' && props.description) {
                        const phoneMatch = String(props.description).match(/(0\d{1,2}[- ]?\d{3,4}[- ]?\d{3,4})/);
                        if (phoneMatch) phone = phoneMatch[0];
                    }

                    calculatedList.push({
                        name: name,
                        phone: phone,
                        address: props.address || props.province || '',
                        distance: parseFloat(dist.toFixed(2)),
                        lat: coords[1],
                        lng: coords[0]
                    });
                }
            });

            // 3. เรียงลำดับจากใกล้ไปไกลที่สุด และเลือกมาเฉพาะ maxResults แห่งแรก
            calculatedList.sort((a, b) => a.distance - b.distance);
            return calculatedList.slice(0, maxResults);

        } catch (err) {
            console.error('❌ Error in getNearbyPoliceStations:', err);
            return [];
        }
    }

    // 📍 วาดหมุด (Point Marker) สถานีตำรวจลงบนแผนที่ Leaflet
    renderPoliceMarkersOnMap(policeList) {
        this.policeMarkerGroup.clearLayers();

        if (!policeList || policeList.length === 0) return;

        policeList.forEach((st, idx) => {
            // สร้าง Custom Icon หรือ CircleMarker สีฟ้า
            const marker = L.circleMarker([st.lat, st.lng], {
                radius: 8,
                fillColor: '#3B82F6',
                color: '#FFFFFF',
                weight: 2,
                opacity: 1,
                fillOpacity: 0.9
            });

            const popupContent = `
                <div style="font-family: sans-serif; font-size: 12px; color: #1e293b;">
                    <b style="color: #1d4ed8;">👮‍♂️ ${idx + 1}. ${st.name}</b><br/>
                    <span style="color: #64748b;">ห่างจากแปลงงาน: ${st.distance} กม.</span><br/>
                    <a href="tel:${st.phone.replace(/[^0-9]/g, '')}" style="display:inline-block; margin-top:4px; color:#2563eb; font-weight:bold; text-decoration:none;">
                        📞 ${st.phone}
                    </a>
                </div>
            `;

            marker.bindPopup(popupContent);
            this.policeMarkerGroup.addLayer(marker);
        });
    }

    getUserPolygonGeoJSON() {
        if (typeof uavPolygonLayer !== 'undefined' && uavPolygonLayer) {
            if (typeof uavPolygonLayer.toGeoJSON === 'function') {
                const geojson = uavPolygonLayer.toGeoJSON();
                return geojson.type === 'FeatureCollection' ? geojson.features[0] : geojson;
            }
            if (typeof uavPolygonLayer.getLayers === 'function') {
                const layers = uavPolygonLayer.getLayers();
                for (let l of layers) {
                    if (typeof l.toGeoJSON === 'function') return l.toGeoJSON();
                }
            }
        }

        if (typeof uavPoint !== 'undefined' && Array.isArray(uavPoint) && uavPoint.length >= 3) {
            const coords = uavPoint.map(pt => [
                Array.isArray(pt) ? pt[1] : (pt.lng !== undefined ? pt.lng : pt[1]),
                Array.isArray(pt) ? pt[0] : (pt.lat !== undefined ? pt.lat : pt[0])
            ]);
            coords.push(coords[0]);
            return turf.polygon([coords]);
        }

        return null;
    }

    async analyzeOverlap() {
        const contentDiv = document.getElementById('noflySidebarContent');
        if (!contentDiv) return;

        let userPolyGeoJSON = this.getUserPolygonGeoJSON();

        if (!userPolyGeoJSON) {
            contentDiv.innerHTML = `
                <div class="p-3 bg-amber-950/40 border border-amber-600/50 rounded-lg text-amber-300">
                    ⚠️ <b>ไม่พบพื้นที่งานบิน:</b> กรุณาวาดหรือกำหนดแปลงงาน UAV บนแผนที่ก่อนทำการตรวจสอบ
                </div>
            `;
            return;
        }

        contentDiv.innerHTML = `
            <div class="p-3 text-slate-300 italic flex items-center gap-2">
                <span class="animate-spin">⏳</span> กำลังวิเคราะห์เขตห้ามบินและค้นหา สภ./สน....
            </div>
        `;

        // 1. ตรวจสอบพื้นที่ห้ามบิน
        let matchedZones = [];
        const checkFeatures = (geoJsonData, category) => {
            if (!geoJsonData || !geoJsonData.features) return;
            geoJsonData.features.forEach(feature => {
                try {
                    if (turf.booleanIntersects(userPolyGeoJSON, feature)) {
                        matchedZones.push({ category, properties: feature.properties || {} });
                    }
                } catch (err) {}
            });
        };

        checkFeatures(this.pdrData1, 'PDR');
        checkFeatures(this.pdrData2, 'PDR');
        checkFeatures(this.aeroData, 'Aerodrome');

        // 2. ค้นหา สภ./สน. ใกล้เคียง (จำกัดเหลือ 3 แห่ง)
        const policeList = this.getNearbyPoliceStations(userPolyGeoJSON, 3, 25);

        // 3. ปักหมุดตำรวจลงแผนที่
        this.renderPoliceMarkersOnMap(policeList);

        // 4. แสดงผลลง Sidebar
        this.renderSidebarResults(matchedZones, policeList);
    }

    renderSidebarResults(zones, policeList) {
        const contentDiv = document.getElementById('noflySidebarContent');
        if (!contentDiv) return;

        let html = '';

        // --- ส่วนที่ 1: เขตห้ามบิน (จัดไว้บนสุด) ---
        html += `<div class="mb-4">`;
        if (zones.length === 0) {
            html += `
                <div class="p-3 bg-green-950/40 border border-green-600/50 rounded-lg text-green-300">
                    ✅ <b>Safe:</b> No restricted airspace detected.
                </div>
            `;
        } else {
            html += `
                <div class="p-2.5 bg-red-950/50 border border-red-600/60 rounded-lg text-red-200 mb-3 font-semibold">
                    🚨 ${zones.length} Restricted Zones Overlapped:
                </div>
            `;

            zones.forEach((item, index) => {
                const props = item.properties;
                const isPDR = item.category === 'PDR';
                const name = props.name || props.NAME || props.Name || 'ไม่มีชื่อระบุ';
                const type = props.type || props.TYPE || props.Type || '';
                const alt = props.alt_limit || props.ALTITUDE || props.Altitude || '';

                html += `
                    <div class="p-3 bg-slate-800 rounded-lg border-l-4 ${isPDR ? 'border-red-500' : 'border-amber-500'} shadow-md mb-2">
                        <div class="font-bold text-sm text-white mb-1">${index + 1}. ${name}</div>
                        <div class="space-y-0.5 text-slate-300">
                            ${type ? `<div><span class="text-slate-400">ประเภท:</span> ${type}</div>` : ''}
                            ${alt ? `<div><span class="text-slate-400">จำกัดความสูง:</span> ${alt}</div>` : ''}
                        </div>
                    </div>
                `;
            });
        }
        html += `</div>`;

        // --- ส่วนที่ 2: รายชื่อสถานีตำรวจใกล้เคียง (แสดงล่างสุด 3 แห่ง) ---
        html += `
            <div class="p-3 bg-blue-950/60 border border-blue-600/50 rounded-lg shadow-md mb-4">
                <div class="flex justify-between items-center font-bold text-blue-300 text-sm mb-2 border-b border-blue-800 pb-1.5">
                    <div class="flex items-center gap-1.5">
                        <span>👮‍♂️</span> Nearby Police Stations (${policeList.length})
                    </div>
                </div>
        `;

        if (policeList && policeList.length > 0) {
            html += `<div class="space-y-2.5">`;
            policeList.forEach((st, idx) => {
                html += `
                    <div class="p-2 bg-slate-800/80 rounded border border-slate-700/60 space-y-1">
                        <div class="font-semibold text-white text-xs flex justify-between items-center">
                            <span>${idx + 1}. ${st.name}</span>
                            <span class="text-blue-400 font-bold text-[11px]">${st.distance} กม.</span>
                        </div>
                        ${st.address ? `<div class="text-slate-400 text-[10px] leading-tight">${st.address}</div>` : ''}
                        <div class="flex justify-end items-center pt-1">
                            <a href="tel:${st.phone.replace(/[^0-9]/g, '')}" class="bg-blue-600 hover:bg-blue-500 text-white px-2 py-0.5 rounded text-[10px] font-medium flex items-center gap-1 transition">
                                📞 ${st.phone}
                            </a>
                        </div>
                    </div>
                `;
            });
            html += `</div>`;
        } else {
            html += `<div class="text-slate-400 italic">No local police stations found in the nearby radius.</div>`;
        }
        html += `</div>`;

        contentDiv.innerHTML = html;
    }

    openSidebar() {
        const sb = document.getElementById('noflySidebar');
        if (sb) sb.classList.remove('translate-x-full');
    }

    closeSidebar() {
        const sb = document.getElementById('noflySidebar');
        if (sb) sb.classList.add('translate-x-full');
    }
}