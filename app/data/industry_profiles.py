INDUSTRY_KEYWORDS = {
    "electrical_contracting": ["electric", "electrical", "contractor", "commercial wiring", "substation"],
    "manufacturing": ["manufacturing", "plant", "factory", "production line", "machining"],
    "construction": ["construction", "general contractor", "build", "site work"],
    "logistics": ["distribution", "warehouse", "fleet", "shipping", "3pl"],
    "healthcare": ["clinic", "hospital", "patient", "provider", "medical"],
    "software": ["saas", "platform", "software", "cloud", "application"],
}

SECTOR_DETAILS = {
    "electrical_contracting": {
        "label": "Electrical Contracting",
        "software": [
            "Procore",
            "Autodesk Construction Cloud",
            "Accubid/Trimble Estimation",
            "ServiceTitan",
            "Sage 300 Construction and Real Estate",
        ],
        "equipment": [
            "Bucket trucks and service vans",
            "Conduit benders and cable pullers",
            "Power quality analyzers and thermal cameras",
            "Lifts/scaffolding",
            "Jobsite generators and temporary power systems",
        ],
    },
    "manufacturing": {
        "label": "Manufacturing",
        "software": ["SAP S/4HANA", "Oracle NetSuite", "Plex", "Epicor", "Ignition SCADA"],
        "equipment": ["CNC machinery", "Robotics", "Conveyors", "Packaging lines", "QA instrumentation"],
    },
    "construction": {
        "label": "Construction",
        "software": ["Procore", "Autodesk Construction Cloud", "Buildertrend", "Sage Intacct Construction"],
        "equipment": ["Excavators", "Backhoes", "Loaders", "Concrete systems", "Survey equipment"],
    },
    "logistics": {
        "label": "Logistics and Distribution",
        "software": ["Manhattan WMS", "Oracle WMS", "SAP EWM", "Samsara", "Motive"],
        "equipment": ["Forklifts", "Sortation systems", "Racking", "RF scanners", "Dock equipment"],
    },
    "healthcare": {
        "label": "Healthcare",
        "software": ["Epic", "Cerner", "athenahealth", "Kareo", "Workday"],
        "equipment": ["Diagnostic imaging", "Lab analyzers", "Clinical devices", "Sterilization systems"],
    },
    "software": {
        "label": "Software",
        "software": ["AWS", "Azure", "GitHub", "Jira", "Datadog"],
        "equipment": ["Developer workstations", "CI/CD infrastructure", "Testing devices", "Networking gear"],
    },
}

EXPANSION_KEYWORDS = [
    "new facility",
    "expansion",
    "expanded",
    "expanding",
    "headquarters",
    "new headquarters",
    "groundbreaking",
    "new location",
    "new plant",
    "new warehouse",
    "new office",
    "opened",
    "opening",
    "construction",
    "capital investment",
    "footprint",
    "square foot",
    "sq ft",
]

PROPERTY_KEYWORDS = [
    "acquired",
    "purchased",
    "real estate",
    "property",
    "warehouse",
    "building",
    "facility",
    "campus",
]

RD_KEYWORDS = [
    "automation",
    "prototype",
    "engineering",
    "design",
    "development",
    "integration",
    "innovation",
    "custom software",
    "process improvement",
]
