"""FAKE peb_lever_jobs.py for the hub's tests: the shape of steltic_india's job specs, two of the PEB golds."""

JOB_SPECS = {
    "IN_Ex1_PEB_factory_shed_tapered_Bengaluru": {
        "job": "IN_Ex1_PEB_factory_shed_tapered_Bengaluru",
        "dado_height_mm": 3000.0, "base": "pinned",
        "seismic": {"zone": "II", "I": 1.0, "R_consts": {"R_Y": "y", "R_X": "x"}, "route": "OMRF"},
    },
    "IN_Ex4_PEB_warehouse_SMRF_Zone4_NCR": {
        "job": "IN_Ex4_PEB_warehouse_SMRF_Zone4_NCR",
        "dado_height_mm": 3000.0, "base": "fixed",
        "seismic": {"zone": "IV", "I": 1.0, "R_consts": {"R_Y": "y", "R_X": "x"}, "route": "SMRF_IS18168"},
    },
}
