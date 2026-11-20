
-- CREATE DATABASE IF NOT EXISTS fde_views;
-- USE fde_views;

-- CREATE OR REPLACE
-- SQL SECURITY DEFINER
-- VIEW fde_views.VW_ACTIVE_FLEET AS
-- SELECT
--     `timestamp` AS Fleet_Timestamp,
--     vehicle_gps_latitude AS Latitude,
--     vehicle_gps_longitude AS Longitude,
--     CAST(iot_temperature AS DECIMAL(8,2)) AS Current_Temperature_C,
--     cargo_condition_status AS Cargo_Condition,
--     risk_classification AS Risk_Classification,
--     CAST(delay_probability AS DECIMAL(8,4)) AS Delay_Probability,
--     port_congestion_level AS Port_Congestion_Level,
--     route_risk_level AS Route_Risk_Level,
--     eta_variation_hours AS ETA_Variation_Hours,
--     weather_condition_severity AS Weather_Severity,
--     disruption_likelihood_score AS Disruption_Likelihood
-- FROM legacy_logistics.TBL_SC_FLEET_HIST_RAW;

-- SELECT * FROM VW_ACTIVE_FLEET;

-- CREATE USER 'user_fde_ro'@'localhost'
-- IDENTIFIED BY "AgentPassword@2026";

-- GRANT SELECT 
-- ON fde_views.VW_ACTIVE_FLEET
-- TO 'user_fde_ro'@'localhost';

-- SHOW GRANTS FOR 'user_fde_ro'@'localhost';

