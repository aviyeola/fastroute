from __future__ import annotations


def register(mcp, manager):
    @mcp.prompt()
    def analyze_board_routability() -> str:
        return ("Inspect board layers, rules, net classes, differential pairs, constraints and likely routing complexity. "
                "Return facts, warnings, and missing information. Do not start routing.")

    @mcp.prompt()
    def route_board_safely() -> str:
        return ("Use an approved routing profile. Preserve all electrical constraints. Explain existing-track handling before starting. "
                "Start routing and return the job ID.")

    @mcp.prompt()
    def monitor_routing_job() -> str:
        return ("Poll job status and inspect event history. Summarize meaningful improvements, stagnation and errors. "
                "Do not stop a job unless the user authorized a stop policy.")

    @mcp.prompt()
    def diagnose_unrouted_connections() -> str:
        return ("Read the final report and explain congestion vs blocked classifications, ignored nets and multi-pin caveats. "
                "Recommend which problems are router-parameter candidates versus PCB-design issues.")

    @mcp.prompt()
    def improve_routing_completion() -> str:
        return ("Compare previous runs. Propose a bounded next attempt. Never relax clearance, minimum width, differential gap, "
                "impedance constraints, keepouts or layer restrictions. Request approval before changing any rule or board design data.")

    @mcp.prompt()
    def review_routing_quality() -> str:
        return ("Compare unrouted connections, KiCad DRC, vias, trace length, pair skew/length constraints and report warnings. "
                "Explain trade-offs rather than optimizing one scalar score.")
