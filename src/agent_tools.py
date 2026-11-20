from dotenv import load_dotenv;
from pathlib import Path
from langchain_huggingface import HuggingFaceEmbeddings
from sqlalchemy import create_engine,URL,text
from langchain_core.tools import tool
from functools import lru_cache
import json
import os
import requests

PROJECT_ROOT =  Path(__file__).absolute().parent.parent
# print(PROJECT_ROOT)
ENV = PROJECT_ROOT / ".env"
load_dotenv(ENV)

MODEL_NAME="sentence-transformers/all-MiniLM-L6-v2"
FAISS_INDEX_PATH = (
    PROJECT_ROOT / "data" / "vectorstore" / "sop_faiss"
)
WEATHER_API_TIMEOUT = int(
    os.getenv("WEATHER_API_TIMEOUT", "8")
)


@lru_cache(maxsize=1)
def get_mysql_engine():
    """
    Creates a SQLAlchemy engine using restricted AI credentials.

    The engine is cached so we don't create a new connection
    pool every time the SQL tool is called.
    """
    db_url = URL.create(
        drivername="mysql+pymysql",
        username=os.environ["AGENT_DB_USER"],
        password=os.environ["AGENT_DB_PASSWORD"],
        host=os.environ["AGENT_DB_HOST"],
        port=int(os.environ["AGENT_DB_PORT"]),
        database=os.environ["AGENT_DB_NAME"],
    )

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_recycle=1800
    )

    return engine



# TOOLS 

@tool
def query_telemetry_db(sql_query: str) -> str:
    """
    Executes a read-only SELECT query against the MySQL semantic view fde_views.VW_ACTIVE_FLEET.

    Available columns:
    Fleet_Timestamp,
    Latitude,
    Longitude,
    Current_Temperature_C,
    Cargo_Condition,
    Risk_Classification,
    Delay_Probability,
    Port_Congestion_Level,
    Route_Risk_Level,
    ETA_Variation_Hours,
    Weather_Severity,
    Disruption_Likelihood.

    Always use MySQL SQL syntax. Only one SELECT query against VW_ACTIVE_FLEET is allowed.Max 10 records.
    """

    try:

        engine = get_mysql_engine()

        with engine.connect() as connection:
            # print(sql_query)
            result = connection.execute(
                text(sql_query)
            )
            columns = list(result.keys())
            # .mappings() gives you dictionary-style access to SQL query results using column names rather than numerical indexes
            rows = result.mappings().fetchmany(10)
            if not rows:
                return "No records matched the query criteria."

            # Step 3: Format result
            records = [
                dict(row)
                for row in rows
            ]

            return (
                "--- MYSQL TELEMETRY RESULTS ---\n"
                f"COLUMNS: {', '.join(columns)}\n"
                f"RECORDS:\n"
                f"{json.dumps(records, indent=2, default=str)}\n"
                "--------------------------------"
            )

    except Exception as e:
        return f"MySQL Database Error: {str(e)}"


# ==========================================================
# 5. TOOL 2: FETCH LIVE CORRIDOR CONDITIONS
# ==========================================================

@tool
def fetch_corridor_conditions(
    latitude: float,
    longitude: float
) -> str:
    """
    Fetches current weather conditions for a given vehicle GPS location using Open-Meteo API.
    Returns temperature, humidity, wind speed, precipitation and weather code.
    These are weather indicators, not direct traffic congestion measurements.
    """

    try:
        if not (-90 <= latitude <= 90):
            return "Invalid latitude."

        if not (-180 <= longitude <= 180):
            return "Invalid longitude."

        url = "https://api.open-meteo.com/v1/forecast"

        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,"
                "relative_humidity_2m,"
                "precipitation,"
                "wind_speed_10m,"
                "wind_direction_10m,"
                "weather_code"
            ),
            "timezone": "auto"
        }

        response = requests.get(
            url,
            params=params,
            timeout=WEATHER_API_TIMEOUT
        )

        response.raise_for_status()

        payload = response.json()
        current = payload.get("current", {})

        if not current:
            return "Weather API returned no current conditions."

        temperature = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        precipitation = current.get("precipitation", "N/A")
        wind_speed = current.get("wind_speed_10m", "N/A")
        wind_direction = current.get("wind_direction_10m", "N/A")
        weather_code = current.get("weather_code", "N/A")

        return (
            "--- LIVE CORRIDOR CONDITIONS ---\n"
            f"GPS: {latitude}, {longitude}\n"
            f"Temperature: {temperature} °C\n"
            f"Humidity: {humidity} %\n"
            f"Precipitation: {precipitation} mm\n"
            f"Wind Speed: {wind_speed} km/h\n"
            f"Wind Direction: {wind_direction} degrees\n"
            f"Weather Code: {weather_code}\n"
            "--------------------------------"
        )

    except Exception as e:
        return f"Weather API Communication Failure: {str(e)}"


# ==========================================================
# 6. TOOL 3: SEARCH COLD CHAIN COMPLIANCE SOP
# ==========================================================
try:
    import streamlit as st
except ImportError:
    st = None

# ==========================================================
# 6. TOOL 3: SEARCH COLD CHAIN COMPLIANCE SOP
# ==========================================================


# ----------------------------------------------------------
# A. COMMON RETRIEVER LOADER
# ----------------------------------------------------------

def _build_sop_retriever(model_name: str, index_path: str):
    """
    Loads the HuggingFace embedding model and FAISS index.

    This is the actual expensive initialization function.
    """

    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_community.vectorstores import FAISS

    print(f"Loading embedding model: {model_name}")

    embeddings = HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True}
    )

    print("Loading FAISS vector store...")

    vector_store = FAISS.load_local(
        index_path,
        embeddings,
        allow_dangerous_deserialization=True
    )

    retriever = vector_store.as_retriever(
        search_kwargs={"k": 3}
    )

    print("SOP Retriever initialized successfully.")

    return retriever


# B. STREAMLIT RESOURCE CACHING

if st is not None:

    @st.cache_resource(show_spinner=False)
    def get_cached_sop_retriever(
        model_name: str,
        index_path: str
    ):
        """
        Caches the embedding model and FAISS retriever
        across Streamlit application reruns.
        """

        print("MEMORY SEED: Initializing SOP resources...")

        return _build_sop_retriever(
            model_name,
            index_path
        )


# C. NORMAL PYTHON CACHING

@lru_cache(maxsize=2)
def get_local_sop_retriever(
    model_name: str,
    index_path: str
):
    """
    Caches the retriever when running outside Streamlit.
    """

    return _build_sop_retriever(
        model_name,
        index_path
    )


def get_sop_retriever():
    """
    Selects the caching mechanism based on execution mode.
    """

    index_path = str(FAISS_INDEX_PATH)

    # Streamlit frontend
    if st is not None and st.runtime.exists():

        return get_cached_sop_retriever(
            MODEL_NAME,
            index_path
        )

    # Normal Python execution
    return get_local_sop_retriever(
        MODEL_NAME,
        index_path
    )

@tool
def search_compliance_sop(query: str) -> str:
    """
    Searches locally indexed Cold Chain Logistics SOP documents.

    Retrieves:
    - Temperature compliance thresholds
    - Cold-chain breach procedures
    - Cargo handling rules
    - Delivery and storage guidelines
    - Risk mitigation procedures.
    """

    try:

        # Get cached retriever
        retriever = get_sop_retriever()

        # Perform semantic search
        matched_docs = retriever.invoke(query)

        if not matched_docs:
            return "No matching compliance clauses found."

        formatted_context = "\n\n".join(
            [
                (
                    f"[Source: {doc.metadata.get('source_file', 'SOP')}]\n"
                    f"[Format: {doc.metadata.get('file_format', 'RAW')}]\n"
                    f"[Chunk: {doc.metadata.get('chunk_index', 'N/A')}]\n"
                    f"{doc.page_content}"
                )
                for doc in matched_docs
            ]
        )

        return (
            "--- COLD CHAIN COMPLIANCE SOP CONTEXT ---\n"
            f"{formatted_context}\n"
            "------------------------------------------"
        )

    except Exception as e:

        return f"SOP Retrieval Error: {str(e)}"


# ==========================================================
# 7. REGISTER ALL AGENT TOOLS
# ==========================================================

AGENT_TOOLS = [
    query_telemetry_db,
    fetch_corridor_conditions,
    search_compliance_sop
]


# ==========================================================
# 8. LOCAL VERIFICATION
# ==========================================================

if __name__ == "__main__":

    print("\n--- TOOL 1: MYSQL TELEMETRY ---")

    print(
        query_telemetry_db.invoke({
            "sql_query": """
                SELECT
                    Latitude,
                    Longitude,
                    Current_Temperature_C,
                    Risk_Classification
                FROM fde_views.VW_ACTIVE_FLEET
                LIMIT 5
            """
        })
    )

    # print("\n--- TOOL 2: LIVE CORRIDOR WEATHER ---")

    # print(
    #     fetch_corridor_conditions.invoke({
    #         "latitude": 19.0760,
    #         "longitude": 72.8777
    #     })
    # )

    print("\n--- TOOL 3: COLD CHAIN SOP RETRIEVAL ---")

    print(
        search_compliance_sop.invoke({
            "query": (
                "What are the temperature limits "
                "and breach procedures for perishable cargo?"
            )
        })
    )
