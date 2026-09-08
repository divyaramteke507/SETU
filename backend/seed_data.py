"""
SETU Seed Data — 20 fictional Rampur flood reports.

Disaster: Urban flooding in Rampur, India.
Time window: 90 minutes starting 2026-09-07T08:00:00+05:30.
Sources: WhatsApp, SMS, Web, Field Worker.
Languages: English (en), Devanagari Hindi (hi), Romanized Hindi (hi-Latn).
Locations: 4 geographic areas — Civil Lines, Kotwali, Bilaspur Chowk, Naya Mohalla.
Expected clustering: 13 candidate incidents across 4 geographic response zones.

Built-in contradictions for testing:
  - Cluster 2 (Kotwali): R008 says 3 people vs R010 says 5 members (numeric)
  - Cluster 3 (Bilaspur Chowk): R015 says "minor" vs R011 says "completely blocked" (severity)
  - Cluster 4 (Naya Mohalla): R018 says boundary wall is intact/no damage vs R019/R020 reporting wall leaning/cracking (hazard_structural)

No real personal information is used. All names, IDs, and details are fictional.
"""

SEED_REPORTS = [
    # =========================================================================
    # CLUSTER 1: Civil Lines — Heavy flooding, multiple streets submerged
    # =========================================================================
    {
        "id": "R001",
        "source": "whatsapp",
        "raw_text": (
            "Heavy flooding near Civil Lines area. Water level rising rapidly, "
            "at least knee-deep on main road. Several shops waterlogged. "
            "People wading through water to get home."
        ),
        "received_at": "2026-09-07T08:00:00+05:30",
        "language": "en",
        "reporter_id": "WA-4821",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R002",
        "source": "sms",
        "raw_text": (
            "सिविल लाइन्स में बहुत पानी भर गया है। सड़क पर घुटनों तक पानी है। "
            "दुकानें बंद हो गई हैं। लोग परेशान हैं।"
        ),
        "received_at": "2026-09-07T08:05:00+05:30",
        "language": "hi",
        "reporter_id": "SMS-1192",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R003",
        "source": "web",
        "raw_text": (
            "Flooding reported in Civil Lines, Rampur. Multiple streets submerged. "
            "Approximately 20 families affected. Water entered ground floor of "
            "residential buildings. Situation worsening."
        ),
        "received_at": "2026-09-07T08:12:00+05:30",
        "language": "en",
        "reporter_id": "WEB-0337",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R004",
        "source": "whatsapp",
        "raw_text": (
            "Civil Lines mein bahut paani bhar gaya hai. Sadak pe chalna mushkil hai. "
            "Kuch logon ko madad chahiye. Paani ghar mein aa raha hai."
        ),
        "received_at": "2026-09-07T08:18:00+05:30",
        "language": "hi-Latn",
        "reporter_id": "WA-7753",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R005",
        "source": "field_worker",
        "raw_text": (
            "On-ground assessment at Civil Lines. Water depth approximately 3 feet on "
            "main road. 15 to 20 houses waterlogged. Elderly residents need assistance. "
            "Drain overflow is the primary cause. No immediate life threat observed."
        ),
        "received_at": "2026-09-07T08:25:00+05:30",
        "language": "en",
        "reporter_id": "FW-0012",
        "gps_lat": 28.7950,
        "gps_lon": 79.0250,
    },

    # =========================================================================
    # CLUSTER 2: Kotwali — Rescue situation, people trapped
    # =========================================================================
    {
        "id": "R006",
        "source": "whatsapp",
        "raw_text": (
            "Family trapped in basement near Kotwali thana! Water rising fast, "
            "need rescue immediately! They are screaming for help. "
            "Situation is very critical."
        ),
        "received_at": "2026-09-07T08:03:00+05:30",
        "language": "en",
        "reporter_id": "WA-2109",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R007",
        "source": "sms",
        "raw_text": (
            "कोतवाली के पास एक परिवार बेसमेंट में फंसा है। तुरंत मदद भेजो। "
            "बच्चे और बुज़ुर्ग हैं। पानी बहुत तेज़ी से बढ़ रहा है।"
        ),
        "received_at": "2026-09-07T08:08:00+05:30",
        "language": "hi",
        "reporter_id": "SMS-5567",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R008",
        "source": "whatsapp",
        "raw_text": (
            "Kotwali ke paas ek ghar mein 3 log phase hain. "
            "Paani bahut tez aa raha hai. Bachao! "
            "Unke ghar ka basement mein paani bhar gaya hai."
        ),
        "received_at": "2026-09-07T08:15:00+05:30",
        "language": "hi-Latn",
        "reporter_id": "WA-3341",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R009",
        "source": "web",
        "raw_text": (
            "Urgent: Rescue needed near Kotwali police station area. "
            "Reports of people trapped in a residential building. "
            "Floodwater has entered the ground floor. Situation critical. "
            "Emergency services requested."
        ),
        "received_at": "2026-09-07T08:22:00+05:30",
        "language": "en",
        "reporter_id": "WEB-0891",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R010",
        "source": "field_worker",
        "raw_text": (
            "Kotwali area: Confirmed 1 family of 5 members including 2 children "
            "and 1 elderly woman trapped in ground floor. Water level at 4 feet "
            "and rising. Immediate rescue operation required. "
            "Building structure appears stable."
        ),
        "received_at": "2026-09-07T08:35:00+05:30",
        "language": "en",
        "reporter_id": "FW-0045",
        "gps_lat": 28.8010,
        "gps_lon": 79.0180,
    },

    # =========================================================================
    # CLUSTER 3: Bilaspur Chowk — Road blockage, waterlogging
    # =========================================================================
    {
        "id": "R011",
        "source": "whatsapp",
        "raw_text": (
            "Road completely blocked near Bilaspur Chowk due to heavy waterlogging. "
            "Vehicles stuck in water. Traffic at standstill. "
            "People abandoning cars and walking."
        ),
        "received_at": "2026-09-07T08:10:00+05:30",
        "language": "en",
        "reporter_id": "WA-6284",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R012",
        "source": "sms",
        "raw_text": (
            "बिलासपुर चौक पर सड़क बंद है। पानी भरा हुआ है। "
            "गाड़ियाँ फंसी हैं। कोई निकल नहीं पा रहा।"
        ),
        "received_at": "2026-09-07T08:20:00+05:30",
        "language": "hi",
        "reporter_id": "SMS-8834",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R013",
        "source": "web",
        "raw_text": (
            "Waterlogging at Bilaspur Chowk intersection causing severe traffic disruption. "
            "Drain overflow suspected as the cause. Water level approximately 2 feet. "
            "Municipal team has been informed."
        ),
        "received_at": "2026-09-07T08:30:00+05:30",
        "language": "en",
        "reporter_id": "WEB-0456",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R014",
        "source": "whatsapp",
        "raw_text": (
            "Bilaspur Chowk pe road block hai. Paani mein 2-3 gaadi phase hain. "
            "Koi aao madad karo. Log pareshaan hain."
        ),
        "received_at": "2026-09-07T08:40:00+05:30",
        "language": "hi-Latn",
        "reporter_id": "WA-1127",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R015",
        "source": "sms",
        "raw_text": (
            "Bilaspur Chowk area waterlogged. Minor flooding only. "
            "No immediate danger to life. Vehicles can pass slowly on side road."
        ),
        "received_at": "2026-09-07T08:55:00+05:30",
        "language": "en",
        "reporter_id": "SMS-2290",
        "gps_lat": None,
        "gps_lon": None,
    },

    # =========================================================================
    # CLUSTER 4: Naya Mohalla — Power outage, structural concern
    # =========================================================================
    {
        "id": "R016",
        "source": "whatsapp",
        "raw_text": (
            "Power cut in Naya Mohalla since morning. Transformer submerged in "
            "flood water. Very dangerous situation! Wires hanging near water. "
            "Keep children away."
        ),
        "received_at": "2026-09-07T08:07:00+05:30",
        "language": "en",
        "reporter_id": "WA-9910",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R017",
        "source": "sms",
        "raw_text": (
            "नया मोहल्ला में बिजली गुल है। ट्रांसफॉर्मर पानी में डूबा है। "
            "खतरनाक स्थिति है। बिजली विभाग को बुलाओ।"
        ),
        "received_at": "2026-09-07T08:14:00+05:30",
        "language": "hi",
        "reporter_id": "SMS-4401",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R018",
        "source": "web",
        "raw_text": (
            "Naya Mohalla residents report power outage due to flooded transformer. "
            "Local volunteer inspected the community center boundary wall and reports "
            "the wall appears fully intact with no visible cracks or damage. "
            "About 50 residents in the area are affected by power cut. "
            "No injuries reported so far."
        ),
        "received_at": "2026-09-07T08:28:00+05:30",
        "language": "en",
        "reporter_id": "WEB-0672",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R019",
        "source": "whatsapp",
        "raw_text": (
            "Naya Mohalle mein bijli nahi hai subah se. Ek deewar tedi ho gayi hai "
            "paani ki wajah se. Log dar rahe hain ki gir jayegi. "
            "Lagbhag 30 log aas paas mein hain."
        ),
        "received_at": "2026-09-07T08:42:00+05:30",
        "language": "hi-Latn",
        "reporter_id": "WA-5578",
        "gps_lat": None,
        "gps_lon": None,
    },
    {
        "id": "R020",
        "source": "field_worker",
        "raw_text": (
            "Naya Mohalla assessment: Power transformer completely flooded, entire area "
            "without electricity. One boundary wall showing significant cracks and leaning, "
            "risk of collapse is high. Approximately 30 residents in immediate vicinity. "
            "Recommend evacuation of adjacent houses."
        ),
        "received_at": "2026-09-07T09:00:00+05:30",
        "language": "en",
        "reporter_id": "FW-0078",
        "gps_lat": 28.7930,
        "gps_lon": 79.0100,
    },
]


def seed_database(db_session) -> int:
    """
    Insert all 20 seed reports into the database if not already present.
    Returns the number of reports inserted.
    """
    from models import Report

    inserted = 0
    for report_data in SEED_REPORTS:
        existing = db_session.query(Report).filter_by(id=report_data["id"]).first()
        if existing is None:
            report = Report(**report_data)
            db_session.add(report)
            inserted += 1

    if inserted > 0:
        db_session.commit()

    return inserted
