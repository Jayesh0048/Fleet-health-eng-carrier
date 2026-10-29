import re
from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class ParsedDVIRResult:
    """Strict data contract for the NLP parser output."""
    subsystem: str
    affected_subsystems: List[str]
    severity: float
    urgency: str
    has_defect: bool
    detected_symptoms: List[str]
    primary_defect_count: int


class AdvancedDVIRNLPParser:
    """
    Highly optimized, memory-resident NLP parser for driver inspection logs.
    Pre-compiles lexicons into unified regex state machines for low-latency batch processing.
    """

    # Added common phonetic typos (e.g., breaks vs brakes, coolent vs coolant)
    SYMPTOM_LEXICON: Dict[str, List[str]] = {
        "BRAKES": [
            r"soft\s+br[ae]kes?",
            r"air\s+leaks?",
            r"slack\s+adjusters?",
            r"squeal(?:ing)?",
            r"br[ae]ke\s+smell",
            r"pulls?\s+left",
            r"pulls?\s+right",
            r"linings?",
            r"pads?\s+worn",
            r"grind(?:ing)?",
        ],
        "COOLING": [
            r"cool[ae]nt(?:\s+leaks?)?",
            r"temp(?:erature)?\s+high",
            r"overheat(?:ing)?",
            r"radiator\s+leak(?:ing|s)?",
            r"sweet\s+smell",
            r"water\s+pump",
            r"hose\s+leak(?:ing|s)?",
            r"antifreeze",
        ],
        "AIR_SYSTEM": [
            r"psi\s+drops?",
            r"governor",
            r"slow\s+build",
            r"air\s+compressors?",
            r"purge\s+valves?",
            r"low\s+air\s+pressure",
            r"air\s+tanks?",
        ],
        "TIRES": [
            r"tread\s+low",
            r"vibrat(?:ion|ing)",
            r"sidewalls?",
            r"valve\s+stems?",
            r"low\s+psi",
            r"tire\s+heat",
            r"blowouts?",
            r"flat\s+tires?",
            r"tread\s+separation",
        ],
        "AFTERTREATMENT": [
            r"def\s+lights?",
            r"regens?",
            r"dpf",
            r"nox(?:\s+sensors?)?",
            r"scr\s+derates?",
            r"exhaust\s+smell",
            r"soot",
            r"emissions?\s+warn(?:ing)?",
        ],
    }

    CRITICAL_INDICATORS: List[str] = [
        r"danger(?:ous)?",
        r"derates?",
        r"smoke",
        r"grind(?:ing)?",
        r"shut(?:ting)?\s+down",
        r"blown?",
        r"flash(?:ing)?",
        r"fails?(?:ure)?",
        r"fire",
        r"locked\s+up",
    ]

    # PRE-COMPILATION BLOCK: Merges arrays into single, fast OR-gated regex patterns
    COMPILED_LEXICONS: Dict[str, re.Pattern] = {
        sys: re.compile(r"\b(?:" + "|".join(pats) + r")\b", re.IGNORECASE)
        for sys, pats in SYMPTOM_LEXICON.items()
    }

    COMPILED_CRITICAL = re.compile(
        r"\b(?:" + "|".join(CRITICAL_INDICATORS) + r")\b", re.IGNORECASE
    )

    NEGATION_PATTERN = re.compile(
        r"\b(?:no|not|without|never|none|zero|passed)\b(?:\s+[\w\-]+){0,4}\s+$",
        re.IGNORECASE,
    )

    NEGATION_OVERRIDE_PATTERN = re.compile(
        r"\b(?:no\s+doubt|no\s+question|no\s+delay)\b", re.IGNORECASE
    )

    CLAUSE_SPLIT_PATTERN = re.compile(
        r"[,.;\n]+|\b(?:but|however|except|yet|although)\b", re.IGNORECASE
    )

    def _is_negated(self, text_before_kw: str) -> bool:
        if self.NEGATION_OVERRIDE_PATTERN.search(text_before_kw):
            return False
        return bool(self.NEGATION_PATTERN.search(text_before_kw))

    def parse(self, notes: str) -> ParsedDVIRResult:
        if not notes or not notes.strip():
            return ParsedDVIRResult(
                subsystem="NONE",
                affected_subsystems=[],
                severity=0.0,
                urgency="NORMAL",
                has_defect=False,
                detected_symptoms=[],
                primary_defect_count=0,
            )

        clean_text = notes.strip()
        detected_symptoms: List[str] = []
        scores_by_system: Dict[str, int] = {k: 0 for k in self.COMPILED_LEXICONS}
        affected_systems: Set[str] = set()
        emergency_hits = 0

        clauses = self.CLAUSE_SPLIT_PATTERN.split(clean_text)

        for raw_clause in clauses:
            clause = raw_clause.strip()
            if not clause:
                continue

            # 1. Single combined regex check per subsystem (O(1) compilations)
            for system, compiled_pattern in self.COMPILED_LEXICONS.items():
                for match in compiled_pattern.finditer(clause):
                    prefix = clause[: match.start()]
                    if not self._is_negated(prefix):
                        matched_text = match.group(0)
                        scores_by_system[system] += 1
                        affected_systems.add(system)
                        detected_symptoms.append(f"{system}: {matched_text}")

            # 2. Single combined regex check for all critical indicators
            for match in self.COMPILED_CRITICAL.finditer(clause):
                prefix = clause[: match.start()]
                if not self._is_negated(prefix):
                    emergency_hits += 1

        best_system = "GENERAL"
        max_matches = 0
        for sys_name, count in scores_by_system.items():
            if count > max_matches:
                max_matches = count
                best_system = sys_name

        if max_matches == 0 and emergency_hits == 0:
            return ParsedDVIRResult(
                subsystem="NONE",
                affected_subsystems=[],
                severity=0.0,
                urgency="NORMAL",
                has_defect=False,
                detected_symptoms=[],
                primary_defect_count=0,
            )

        multi_system_penalty = 0.10 if len(affected_systems) > 1 else 0.0
        base_severity = min(0.60, max_matches * 0.25)
        critical_severity = min(0.40, emergency_hits * 0.20)
        final_severity = round(
            min(1.0, max(0.20, base_severity + critical_severity + multi_system_penalty)), 2
        )

        if final_severity >= 0.70:
            urgency = "CRITICAL"
        elif final_severity >= 0.35:
            urgency = "MODERATE"
        else:
            urgency = "MINOR"

        return ParsedDVIRResult(
            subsystem=best_system,
            affected_subsystems=sorted(list(affected_systems)),
            severity=final_severity,
            urgency=urgency,
            has_defect=True,
            detected_symptoms=detected_symptoms,
            primary_defect_count=max_matches,
        )