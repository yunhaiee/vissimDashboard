"""The Vissim ``.inpx`` schema constants the emitter writes — every one MEASURED.

Why this module exists
----------------------
Emitter v1 was built against ``data/practice.inpx``, which the 2026-08-06 live
check proved is a hand-written 6 KB exemplar and **not Vissim output** (its
``version="2400"`` is not a Vissim schema version at all). Files written to that
shape are refused by ``LoadNet`` with "The network file could not be loaded.",
and no ``.err`` is written. The probe bisected the refusal in both directions:
a REAL network stripped to v1's attribute set becomes unloadable with the
identical error, and v1's own geometry grafted into a real skeleton LOADS.

So every constant below is copied VERBATIM out of a network **PTV Vissim itself
saved**, and each one names its source file. Nothing here is inferred, rounded
or "sensible-looking"; where a value could not be measured it is not written.

The sources (all under ``runtime/genesis-open-live-check/nets/``, produced by
``backend/tools/genesis_open_live_check.py`` on PTV Vissim Academic 2026 SP 09)
---------------------------------------------------------------------------
``RT``   ``rt_generated/rt_generated_geometry_saved.inpx`` — Vissim's OWN save of
         this generator's crossroads geometry (20 links, 1 node). The single
         best authority for our own element shapes: it is literally what Vissim
         writes when it holds this network.
``LHT``  ``ref_lht/Roundabout London_saved.inpx`` — Vissim's save of the only
         LEFT-HAND-TRAFFIC example that ships with Vissim. Authority for
         ``netPara`` (``trafReg="LEFTHANDTRAFFIC"``) and for the base objects
         numbered 1, which is also what Vissim's factory ``defaults.inpx``
         numbers them.
``NOD``  ``ref_nodes/3 Roundabouts - Conflict areas_saved.inpx`` — Vissim's save
         of the smallest example that HAS ``<node>`` elements. Authority for the
         ``<node>`` attribute set.

Two measurements that decide shape rather than values
-----------------------------------------------------
* A connector's endpoints are ``<fromLinkEndPt link="32" pos="…"/>`` — attribute
  ``link``, **no lane token**. The ``"<link> <lane>"`` token lives in
  ``<objectRef key="32 2"/>`` under each connector lane's
  ``<fromLanes>``/``<toLanes>``: one ``<lane>`` element per connector lane, each
  mapping exactly one approach lane to exactly one exit lane.
* No Vissim-saved network anywhere on this machine contains ``<linkSegment>`` or
  ``<linkSegs>``. Node membership is NOT declared in the file; it is geometric,
  and ``services.inpx_parser._node_link_ids`` now computes it that way.

What is deliberately NOT copied
-------------------------------
``refPointMap``/``refPointNet`` (both references carry them) anchor a network to
the map. This generator's geometry is a local tangent plane with no georeference,
so writing them would be an absolute claim the draft does not make. ``netPara``
is emitted childless for the same reason — ``LHT``'s ``<defRefsElevator>``
references displayTypes 6–10 and areaBehaviorTypes 1–2, objects this emission
does not create, and a dangling reference is worse than an absent default.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

#: Source: RT/LHT/NOD root element — all three Vissim-saved files agree.
#: v1 wrote ``version="2400"``, copied from the hand-written practice.inpx.
NETWORK_VERSION = "1403"

#: Source: RT root ``vissimVersion`` (the build that saved our own geometry).
#: Vissim accepts a file whose vissimVersion is older than the running build —
#: the reference networks shipped as 2026.00-07 and loaded on SP 09 — so this is
#: a provenance stamp, not a compatibility gate.
VISSIM_VERSION = "2026.00-09 [307071]"


# ---------------------------------------------------------------------------
# netPara
# ---------------------------------------------------------------------------

#: Source: LHT ``<netPara>``, verbatim, MINUS the two ``refPoint*`` children and
#: with ``trafReg`` supplied by the caller from the draft's own declaration.
#: Everything else is byte-identical to what Vissim wrote for the left-hand
#: example. ``northDir="0"`` states that +y is north IN THIS FILE'S OWN FRAME,
#: which is exactly what the projection produces.
NETPARA_ATTRS: dict[str, str] = {
    "concatMaxLen": "255",
    "concatSeparator": ",",
    "drivSimActive": "false",
    "linkGradBas": "ZCOORDINATES",
    "northDir": "0",
    "odrFilePath": "",
    "pedDefCellSize": "0.15",
    "pedDefObstDist": "0.5",
    "pedDirChgAngle": "4",
    "pedDirChgClip": "true",
    "pedNbhSearGridSize": "5",
    "pedQueueOrder": "0.7",
    "pedQueueStraight": "0.6",
    "powWghtRatioMax": "30",
    "powWghtRatioMin": "7",
    "radExper": "2",
    "sigHead3DArrowColorRedAmb": "COLOREDARROWONBLACK",
    "speedLimCurves": "true",
    "underScenMngm": "false",
    "unitAccel": "METERSPERSQUARESECOND",
    "unitJerk": "METERSPERCUBICSECOND",
    "unitLenLong": "KILOMETERS",
    "unitLenShort": "METERS",
    "unitLenVeryShort": "MILLIMETERS",
    "unitSpeed": "KILOMETERSPERHOUR",
    "unitSpeedSmall": "METERSPERSECOND",
}

#: The two values Vissim's ``trafReg`` takes. Source: LHT (LEFTHANDTRAFFIC) and
#: NOD/RT (RIGHTHANDTRAFFIC). Measured consequence: **without** it a generated
#: network opens as right-hand traffic no matter which side the geometry puts
#: each carriageway on — the file, not the geometry, decides the regime.
TRAF_REG_LEFT = "LEFTHANDTRAFFIC"
TRAF_REG_RIGHT = "RIGHTHANDTRAFFIC"


# ---------------------------------------------------------------------------
# The ids this emission references, and the objects that declare them
# ---------------------------------------------------------------------------
#
# A real network stripped to ``netPara`` + ``links`` loads with load_ok=True and
# **zero links** (measured: diagnose v5/v6). Vissim drops every link whose
# ``displayType`` / ``level`` / ``linkBehavType`` — and every lane whose
# ``markingType`` — points at an object the file never declares, and reports
# success anyway. The containers below exist to make those four references
# resolve — AND the references those objects make in turn: ``linkBehaviorType 1``
# names a driving behaviour, which names two time distributions. Measured on
# v2's first live run: leave the last two hops out and Vissim drops the whole
# ``drivingBehaviors`` container, then ``linkBehaviorTypes``, then every link,
# and still reports a successful load with no ``.err``. The chain is closed, not
# truncated. Ids (all 1, bar the two distributions) are Vissim's own: LHT numbers
# them so, and so does the factory ``Exe/defaults.inpx`` that File > New starts
# from.

#: Source: LHT ``<levels>``.
LEVEL_NO = "1"
#: Source: LHT ``<displayTypes><displayType no="1">`` ("Road gray").
DISPLAY_TYPE_NO = "1"
#: Source: LHT ``<linkBehaviorTypes><linkBehaviorType no="1">`` ("Urban
#: (motorized)"). §5 names no link behaviour type for a generic urban draft, so
#: Vissim's own urban default is used and the compliance report says so.
LINK_BEHAV_TYPE_NO = "1"
#: Source: LHT ``<laneMarkingTypes><laneMarkingType no="1">`` ("Default").
LANE_MARKING_TYPE_NO = "1"
#: Source: LHT ``<linkBehaviorType no="1" drivBehavDef="1">`` → the driving
#: behaviour that link behaviour type 1 delegates to.
DRIVING_BEHAVIOR_NO = "1"

#: Support containers, verbatim from LHT, in the order Vissim writes them among
#: the emitted set. Each entry is ``(container_tag, (node, …))`` where a node is
#: ``(tag, attrs, (node, …))`` — a nested literal rather than an XML string so
#: that the emitter builds every element through the same ``ElementTree`` path
#: and the tests can assert on attribute SETS rather than on formatting.
#:
#: Only the ids this emission actually references are declared. Declaring the
#: rest of LHT's catalogue would be copying values into a deliverable for no
#: reason a reader could check.
_SUPPORT_CONTAINERS_WITHOUT_BEHAVIOURS: tuple = (
    (
        "displayTypes",
        (
            (
                "displayType",
                {
                    # Source: LHT displayType no="1", every attribute verbatim.
                    "allSidesSame": "false",
                    "anisoFilt": "true",
                    "borderColor": "ff808080",
                    "borderLineStyle": "SOLIDLINE",
                    "curved": "false",
                    "drawOrder3D": "MID",
                    "fillColor": "ff808080",
                    "fillStyle": "SOLIDFILL",
                    "invisible": "false",
                    "name": "Road gray",
                    "no": DISPLAY_TYPE_NO,
                    "noMipmap": "false",
                    "rail": "false",
                    "railFlangeHgt": "0.013",
                    "railFlangeWid": "0.15",
                    "railGauge": "1.435",
                    "railHeadHgt": "0.043",
                    "railHeadWid": "0.073",
                    "railHgt": "0.172",
                    "railType": "STANDARD",
                    "railWebWid": "0.016",
                    "texHorizLen": "1",
                    "textureFilename": "",
                    "tiesHgt": "0.02",
                    "tiesLen": "2.6",
                    "tiesSpac": "0.6",
                    "tiesTexFilename": "",
                    "tiesTexHorizLen": "1",
                    "tiesType": "STANDARD",
                    "tiesWid": "0.26",
                },
                (),
            ),
        ),
    ),
    # ``drivingBehaviors`` sits here, between displayTypes and laneMarkingTypes,
    # in Vissim's own container order; its single entry is DRIVING_BEHAVIOR_1
    # below and is spliced in by :data:`SUPPORT_CONTAINERS` at import time.
    (
        # MEASURED ON THE FIRST v2 LIVE RUN. Without these two, Vissim loaded
        # the file (load_ok=True, no .err) and held ZERO links: it dropped the
        # whole ``drivingBehaviors`` container because DRIVING_BEHAVIOR_1's
        # ``w99cc1Distr="2"`` and ``zipperOvtTmDistr="200"`` pointed at time
        # distributions the file never declared, which made
        # ``linkBehaviorType no="1"`` dangle in turn, which made every link
        # vanish. Vissim's own save of that load is the evidence: it wrote back
        # displayTypes, laneMarkingTypes, levels and the node — and no
        # ``links``, no ``linkBehaviorTypes``, no ``drivingBehaviors``.
        # A one-step reference chain is not a reference chain.
        "timeDistributions",
        (
            (
                "timeDistribution",
                # Source: LHT timeDistribution no="2" — DRIVING_BEHAVIOR_1's
                # ``w99cc1Distr``.
                {"mean": "0.9", "name": "0.9 s", "no": "2", "stdDev": "0", "type": "NORMAL"},
                (),
            ),
            (
                "timeDistribution",
                # Source: LHT timeDistribution no="200" — DRIVING_BEHAVIOR_1's
                # ``zipperOvtTmDistr``. Empirical, so it carries data points.
                {
                    "mean": "3",
                    "name": "2-4 s",
                    "no": "200",
                    "stdDev": "0.5",
                    "type": "EMPIRICAL",
                },
                (
                    (
                        "durDistrDataPts",
                        {},
                        (
                            ("durationDistributionDataPoint", {"fx": "0", "x": "2"}, ()),
                            ("durationDistributionDataPoint", {"fx": "1", "x": "4"}, ()),
                        ),
                    ),
                ),
            ),
        ),
    ),
    (
        "laneMarkingTypes",
        (
            (
                "laneMarkingType",
                # Source: LHT laneMarkingType no="1" "Default".
                {"name": "Default", "no": LANE_MARKING_TYPE_NO},
                (
                    (
                        "lineStyle",
                        {"color": "fff0f0f0", "gapLen": "3", "lineLen": "3", "lineWid": "0.12"},
                        (),
                    ),
                ),
            ),
        ),
    ),
    (
        "levels",
        (
            (
                "level",
                # Source: LHT level no="1" "Base".
                {"name": "Base", "no": LEVEL_NO, "zCoord": "0"},
                (),
            ),
        ),
    ),
    (
        "linkBehaviorTypes",
        (
            (
                "linkBehaviorType",
                # Source: LHT linkBehaviorType no="1".
                {
                    "anmFlag": "false",
                    "drivBehavDef": DRIVING_BEHAVIOR_NO,
                    "name": "Urban (motorized)",
                    "no": LINK_BEHAV_TYPE_NO,
                },
                (),
            ),
        ),
    ),
)

#: Source: LHT ``<drivingBehaviors><drivingBehavior no="1">`` — all 131
#: attributes verbatim. This is the object ``linkBehaviorType no="1"`` delegates
#: to; without it that reference dangles. It is kept in its own constant, and
#: spliced into :data:`SUPPORT_CONTAINERS` below, purely for readability.
DRIVING_BEHAVIOR_1: dict[str, str] = {
    "accDecelOwn": "-1",
    "accDecelTrail": "-1",
    "accDecelTrailFrLnChgMax": "-3",
    "accDecelTrailFrLnChgMin": "-2",
    "accDecelTrailFrLnChgRiseTm": "10",
    "acc_J": "1",
    "acc_MinGapTime": "1",
    "acc_StandSafDist": "5",
    "acc_a_cc_max": "1.5",
    "acc_a_cc_min": "-2",
    "acc_a_s": "-3",
    "acc_a_start": "0.5",
    "acc_d_s": "2.5",
    "acc_delta_res": "0.5",
    "acc_t_s_brake": "10",
    "acc_t_s_max": "3",
    "acc_t_s_min": "1",
    "acc_tau_J_minus": "0.3",
    "acc_tau_J_plus": "1",
    "acc_tau_cc": "5",
    "acc_tau_d": "5",
    "acc_tau_v": "1",
    "acc_v_res": "10.8",
    "alc_T_overtake": "11",
    "alc_T_r": "1.4",
    "alc_T_r_cancel": "0.4",
    "alc_a_d_r_acc": "-3",
    "alc_a_d_r_cancel": "-3.7",
    "alc_a_ego_d_f_acc": "-4",
    "alc_beta": "0.66666666666666663",
    "alc_delta_v_limit": "30",
    "alc_delta_v_min": "5.4",
    "alc_gamma": "2",
    "alc_lambda_LC_cancel": "0.5",
    "alc_tau_d_f_faster": "0.4",
    "alc_tau_d_f_slower": "0.4",
    "alc_tau_d_r_faster": "1",
    "alc_tau_d_r_slower": "1",
    "alc_tau_dot_ramp": "0.5",
    "alc_tau_ego_faster_direct": "1",
    "alc_v_limit_max": "160",
    "amberA": "1.588581",
    "amberB1": "-0.261981",
    "amberB2": "0.269453",
    "amberBehav": "CONTINUOUSCHECK",
    "autLnChg": "false",
    "carFollowModType": "WIEDEMANN74",
    "conflArAsInteractObj": "false",
    "consNextTurn": "false",
    "coopDecel": "-3",
    "coopLnChg": "false",
    "coopLnChgCollTm": "10",
    "coopLnChgSpeedDiff": "10.8",
    "decelRedDistOwn": "100",
    "decelRedDistTrail": "100",
    "desLatPos": "MIDDLE",
    "diamQueu": "false",
    "diffusTm": "60",
    "distractProb": "0",
    "enforcAbsBrakDist": "false",
    "freeDrivTm": "11",
    "incrsAccel": "1",
    "jerkLimit": "true",
    "latDirChgMinTm": "0",
    "latDistDrivDef": "1",
    "latDistStandDef": "0.2",
    "lnChgRule": "FREELANESELECTION",
    "lookAheadDistMax": "250",
    "lookAheadDistMin": "0",
    "lookBackDistMax": "150",
    "lookBackDistMin": "0",
    "maxDecelOwn": "-4",
    "maxDecelTrail": "-3",
    "maxNumPlatoonVeh": "7",
    "maxPlatoonApprDist": "250",
    "maxPlatoonDesSpeed": "80",
    "mesoMaxWaitTm": "120",
    "mesoReactTm": "1.2",
    "mesoStandDist": "2",
    "minCollTmGain": "2",
    "minFrontRearClear": "0.5",
    "minSpeedForLat": "1",
    "name": "Urban (motorized)",
    "no": DRIVING_BEHAVIOR_NO,
    "numInteractObj": "4",
    "numInteractVeh": "99",
    "obsrvAdjLn": "false",
    "ovtLDef": "false",
    "ovtRDef": "false",
    "ovtRedSpeedAreas": "false",
    "platoonFollowUpGapTm": "0.6",
    "platoonMinClear": "2",
    "platoonPoss": "false",
    "rearCorr": "false",
    "rearCorrEnd": "10",
    "rearCorrMaxSpeed": "3",
    "rearCorrStart": "1",
    "recovAcc": "0.4",
    "recovDist": "2000",
    "recovSafDist": "1.1",
    "recovSlow": "false",
    "recovSpeed": "0.6",
    "redAmberBehav": "GO",
    "safDistFactLnChg": "0.6",
    "safDistFactSig": "0.6",
    "safDistFactSigEnd": "100",
    "safDistFactSigStart": "100",
    "sleepDur": "0",
    "sleepProb": "0",
    "slowLnOvt_MaxSpeed": "60",
    "slowLnOvt_MaxSpeedDiff": "20",
    "standDist": "0.5",
    "standDistIsFix": "false",
    "useImplicStoch": "true",
    "vehRoutDecLookAhead": "true",
    "w74ax": "2",
    "w74bxAdd": "2",
    "w74bxMult": "3",
    "w99cc0": "1.5",
    "w99cc1Distr": "2",
    "w99cc2": "4",
    "w99cc3": "-8",
    "w99cc4": "-0.35",
    "w99cc5": "0.35",
    "w99cc6": "11.44",
    "w99cc7": "0.25",
    "w99cc8": "3.5",
    "w99cc9": "1.5",
    "zipper": "true",
    "zipperMinSpeed": "20",
    "zipperOvtTmDistr": "200",
}


#: The support containers in the order Vissim writes them, with the single
#: ``drivingBehaviors`` entry spliced into its own slot. Same shape throughout:
#: ``(container_tag, (node, …))`` with ``node = (tag, attrs, (node, …))``.
SUPPORT_CONTAINERS: tuple = tuple(
    sorted(
        (
            *_SUPPORT_CONTAINERS_WITHOUT_BEHAVIOURS,
            ("drivingBehaviors", (("drivingBehavior", DRIVING_BEHAVIOR_1, ()),)),
        ),
        key=lambda entry: entry[0],
    )
)


# ---------------------------------------------------------------------------
# <link> and <link>-as-connector
# ---------------------------------------------------------------------------

#: The 32 attributes every ``<link>`` carries that this generator does NOT
#: compute, verbatim from RT (Vissim's own save of this generator's own
#: crossroads). The attribute SET is identical on all 139 links of LHT, all 131
#: of NOD and all 20 of RT, and 30 of the 32 VALUES are identical on every one
#: of those 290 elements — which is what makes them Vissim's defaults rather
#: than one network's choices. The two that vary were measured and are both
#: properties of geometry this draft does not have: ``gradient`` (0.040001 on
#: the graded Buehl links; our polylines are planar, every ``zOffset`` 0) and
#: ``thickness`` (0.5 on 3 of LHT's pedestrian links). Only the three id
#: references below — ``displayType``, ``linkBehavType``, ``level`` — differ
#: systematically between files, and each is emitted from its own constant.
#:
#: v1 wrote TWO attributes here (``no`` and ``name``). The probe measured what
#: happens when they are the only ones present: a REAL network with every link
#: reduced to ``no``/``name`` is refused by LoadNet with the same error the
#: generated files got (diagnose2 w1). These are mandatory.
LINK_COMMON_ATTRS: dict[str, str] = {
    "alc_v_limit": "130",
    "assumSpeedOncom": "60",
    "consVehInDynPot": "false",
    "costPerKm": "0",
    "desSpeedFact": "1",
    "direction": "ALL",
    "emergStopDist": "5",
    "emiCalcAct": "true",
    "gradient": "0",
    "hasOvtLn": "false",
    "isPedArea": "false",
    "linkEvalAct": "true",
    "linkEvalSegLen": "10",
    "lnChgDist": "200",
    "lnChgDistIsPerLn": "false",
    "lnChgEvalAct": "true",
    "lookAheadDistOvt": "250",
    "mesoFollowUpGap": "0",
    "mesoSpeed": "50",
    "mesoSpeedModel": "VEHICLEBASED",
    "netPerfEvalAct": "true",
    "ovtOnlyPT": "false",
    "ovtSpeedFact": "1.3",
    "rvsPark": "false",
    "showClsfValues": "true",
    "showLinkBar": "true",
    "showVeh": "true",
    "surch1": "0",
    "surch2": "0",
    "thickness": "0",
    "vehDynPotG": "3",
    "vehRecAct": "true",
}

#: A plain link carries ``level``; a CONNECTOR does not. Measured on all three
#: saved references: every element with ``<fromLinkEndPt>`` lacks ``level``,
#: every element without one has it (LHT 67/72 split, NOD 77/54, RT 8/12).
#: ``rvsPedLink`` is the other link-only attribute; it appears on the 10 LHT
#: pedestrian links only and is a reference to another link, so it is not
#: written here.
LINK_ONLY_ATTRS: dict[str, str] = {"level": LEVEL_NO}

#: Source: RT/LHT/NOD ``<linkPolyPoint>`` — every point carries these three
#: besides x/y. ``rad`` (the fitted curve radius) appears only where Vissim
#: computed one and ``radiusSrc="CALCULATED"`` says it is derived, so writing a
#: ``rad`` of our own would be inventing a number Vissim recomputes anyway.
LINK_POLY_POINT_ATTRS: dict[str, str] = {
    "radiusEffect": "BRAKE",
    "radiusSrc": "CALCULATED",
    "zOffset": "0",
}


# ---------------------------------------------------------------------------
# <node>
# ---------------------------------------------------------------------------

#: Source: NOD ``<node>`` (all 3 identical) and RT ``<node>`` — the same 12
#: non-identifying attributes in both, so they are Vissim's defaults.
NODE_ATTRS: dict[str, str] = {
    "allowRecr": "false",
    "anmConnNodeNo": "0",
    "anmConnZoneNo": "0",
    "anmZoneNo": "0",
    "hasUserDefOrient": "false",
    "mesoPenalMerg": "0",
    "showClsfValues": "true",
    "showTurnValVisual": "false",
    "turnValVisualSize": "70",
    "useForDynAssign": "true",
    "useForEval": "true",
    "useForMeso": "false",
}

#: Source: NOD/RT ``<node><geometry><points><point x= y=/>`` — x and y and
#: NOTHING else. v1 wrote ``zOffset="0"`` on node points too; no Vissim-saved
#: node point on this machine carries one.
NODE_POINT_ATTRS: tuple[str, ...] = ("x", "y")


# ---------------------------------------------------------------------------
# Signals — <signalController>, <signalGroup>, <signalHead>, <detector>
#
# Roadmap 3.1 Genesis (b). Added ADDITIVELY: an unsignalised draft writes none
# of these containers and its bytes are unchanged (pinned by
# ``test_genesis_signals.py::test_unsignalised_draft_is_byte_identical_...``).
#
# Same discipline as everything above, and for the same reason: emitter v1 died
# of an attribute set invented from a hand-written exemplar. Every constant here
# is copied from ``LHT`` — ``ref_lht/Roundabout London_saved.inpx``, which is
# Vissim's own save of the only LEFT-HAND-TRAFFIC example that ships with
# Vissim, and which carries 3 ``<signalController>``, 27 ``<signalHead>`` and 2
# ``<detector>`` elements. LHT is the right authority twice over: it is
# Vissim-authored, and it is left-hand traffic, which is the regime §5.2.1.1
# puts this whole generator in.
#
# Cross-checked against the CONSULTANT network's signal elements for SHAPE only
# (element/attribute names and structure — no value, id, name or timing from it
# is reproduced here or anywhere in this package). Measured agreement:
#
#   * its 6 ``<signalController>`` carry the identical 21-attribute set, and all
#     6 carry ``rampMeterPara`` + ``sgs`` + ``sigTmsTabConfig`` (one also carries
#     a ``wttFiles`` child, which this emission does not write and does not
#     reference);
#   * its 26 ``<signalGroup>`` carry the identical 15-attribute set — the 11
#     constants below plus the four computed ones (``no``, ``amber``,
#     ``redAmber``, ``minGreen``). ``minGreen`` living on the outer element,
#     rather than inside the Vissig blob, is measured on BOTH networks;
#   * its 39 ``<signalHead>`` carry the identical 17-attribute set bar an
#     OPTIONAL ``scndSG`` present on 2 of the 39;
#   * it has no ``<detectors>`` container at all, so LHT is the sole authority
#     for that element.
#
# Two independently-authored networks agreeing on the attribute set is what
# makes these Vissim's defaults rather than one file's choices.
# ---------------------------------------------------------------------------

#: Vissim's signal DISPLAY enum, measured from LHT's decoded ``intSupplyData``:
#: every ``<sg>`` with ``defaultSignalSequence="3"`` carries fixed states
#: ``display="2" duration="2000"`` and ``display="4" duration="3000"`` while its
#: ``<signalGroup>`` element carries ``redAmber="2"`` and ``amber="3"`` — which
#: pins 2=red/amber and 4=amber by the two numbers agreeing. 3=green and 1=red
#: follow from the ``<cmds>``: every group's two commands are exactly one
#: ``display="3"`` and one ``display="1"``, and ``services.signal_parser``
#: (which reads real production networks) reads them as green-start and
#: red-start respectively.
DISPLAY_RED = "1"
DISPLAY_RED_AMBER = "2"
DISPLAY_GREEN = "3"
DISPLAY_AMBER = "4"

#: Vissig's ``defaultSignalSequence`` / ``signal_sequence``. Source: LHT — the 8
#: vehicle groups of controller 24 use ``"3"`` and carry amber + red/amber fixed
#: states; its 2 remaining groups use ``"4"``, carry NO fixed states, and their
#: ``<signalGroup>`` elements state ``amber="0" redAmber="0"``. So 3 is the
#: four-display vehicle sequence and 4 is red/green only. This emission writes 3
#: for every group: MRWA vehicle signals have an amber (A.3 computes it), and a
#: red/amber of zero — see :data:`.signals.SignalSettings.red_amber_s` — is how
#: sequence 3 expresses "no red/amber interval", which is the Australian
#: practice.
SIGNAL_SEQUENCE_VEHICLE = "3"

#: Vissig stores every duration in MILLISECONDS. Source: LHT ``<prog
#: cycletime="100000">`` on a 100 s program whose ``<cmd begin="13000">`` /
#: ``<cmd begin="67000">`` bracket a green that ``services.signal_parser``
#: divides by 1000 to report in seconds.
VISSIG_MS_PER_S = 1000

#: Source: LHT ``<signalController type="FIXEDTIME">`` (nos 24 and 25 —
#: byte-identical on all 21 attributes bar ``no`` and ``intSupplyData``). The
#: caller supplies ``no`` and ``intSupplyData``; everything below is verbatim.
#:
#: ``cycTm="0"`` with ``cycTmIsVar="true"`` is NOT an oversight and is NOT this
#: module rounding something off: both of Vissim's own saved FIXEDTIME
#: controllers write exactly that while their ``<prog>`` carries the real
#: ``cycletime``. For a Vissig fixed-time controller the outer attribute is not
#: the authority — the program is — and this emission puts the cycle where
#: Vissim itself puts it rather than duplicating it into a field Vissim leaves
#: at zero.
SIGNAL_CONTROLLER_ATTRS: dict[str, str] = {
    "active": "true",
    "cycTm": "0",
    "cycTmIsVar": "true",
    "debug": "false",
    "guiFile": "",
    "name": "",
    "offset": "0",
    "progFile": "",
    "progNo": "1",
    "scDetRecLabelDet": "PORTNUMBER",
    "scDetRecLabelSG": "NUMBER",
    "scDetRecShortNam": "false",
    "sigTmsTabAutoConfig": "true",
    "sigTmsTabLabelDet": "PORTNUMBER",
    "sigTmsTabLabelSG": "NUMBER",
    "supplyFile1": "",
    "supplyFile2": "",
    "supplyFile3": "",
    "type": "FIXEDTIME",
}

#: Source: LHT ``<signalController><rampMeterPara>`` — identical on all three of
#: its controllers, including the two that have nothing to do with ramp
#: metering, which is what makes it a default rather than a setting. Written
#: because Vissim writes it on every controller it saves; this generator emits
#: no ramp meter and none of these values is read by anything it does emit.
RAMP_METER_PARA_ATTRS: dict[str, str] = {
    "metEvalInt": "60",
    "metRateQDisp": "0.25",
    "occupDes": "0.25",
    "occupQDispAct": "0.5",
    "occupQDispDeact": "0.1",
    "regPara": "0.019444444444444441",
}

#: Source: LHT ``<sgs><signalGroup>`` — the 11 attributes this emission does not
#: compute, identical across all 20 signal groups of LHT's three controllers.
#: The caller supplies ``no``, ``amber``, ``redAmber`` and ``minGreen``; those
#: four are the only ones that carry a number derived from the plan.
#:
#: ``minGreen`` is NOT here even though LHT writes ``minGreen="0"`` on its
#: fixed-time groups: the plan may carry a real minimum green from the
#: controller record, and §5.4.2.1 requires minimum greens to be "directly input
#: from controller/SCATS information", so it is a computed field.
SIGNAL_GROUP_ATTRS: dict[str, str] = {
    "actuationType": "UNDEFINED",
    "callDetPortNo": "0",
    "chkOutDetPortNo": "0",
    "clearTm": "0",
    "endGreen": "0",
    "endRed": "0",
    "greenFlsh": "0",
    "maxGreen": "0",
    "minRed": "0",
    "name": "",
    "type": "NORMAL",
}

#: Source: LHT ``<sigTmsTabConfig><signalOutputConfigurationElement>`` — the
#: signal-times-table row Vissim writes per signal group. Identical on all 18
#: SIGGRP rows across LHT's controllers bar ``sg`` and ``varNo``, which the
#: caller supplies.
SIGNAL_OUTPUT_CONFIG_ATTRS: dict[str, str] = {
    "configName": "SIGGRP",
    "detPort": "0",
    "title": "",
    "wttFilename": "",
}

#: Source: LHT ``<signalHeads><signalHead>`` — the 12 attributes this emission
#: does not compute, identical across all 27 of them. The caller supplies
#: ``no``, ``localNo``, ``lane`` (``"<link> <lane>"``), ``pos`` and ``sg``
#: (``"<controller> <group>"``).
#:
#: ``type="CIRCULAR"`` is the ordinary vehicle head: all 27 LHT heads are
#: CIRCULAR. ``anmFlag="false"`` here against ``"true"`` on the detectors below
#: is measured, not a typo — the two elements genuinely differ.
SIGNAL_HEAD_ATTRS: dict[str, str] = {
    "allPedTypes": "true",
    "allVehTypes": "true",
    "anmFlag": "false",
    "complRate": "1",
    "dischRecAct": "false",
    "isBlockSig": "false",
    "name": "",
    "scndSGTyp": "ORSG",
    "slowDownDist": "3",
    "type": "CIRCULAR",
    "vAmberBlock": "0",
}

#: Source: LHT ``<detectors><detector>`` — the 9 attributes this emission does
#: not compute, identical on both. The caller supplies ``no``, ``portNo``,
#: ``lane``, ``pos``, ``length`` and ``sc``.
#:
#: ``length`` and ``pos`` are deliberately NOT taken from LHT (whose detectors
#: are 1.5 m long): §5.4.2.6 states the length and the setback this generator
#: must use, so those two numbers come from the guideline and LHT contributes
#: only the attribute set around them. That split — Vissim owns the defaults,
#: MRWA owns the engineering numbers — is the rule for this whole block.
DETECTOR_ATTRS: dict[str, str] = {
    "allPedTypes": "true",
    "allVehTypes": "true",
    "anmFlag": "true",
    "maxPedSpeed": "3.6",
    "name": "",
    "smthFactDecr": "0.25",
    "smthFactIncr": "0.25",
    "soundFile": "",
    "tmBefDep": "0",
    "type": "STANDARD",
}


# ---------------------------------------------------------------------------
# Container order
# ---------------------------------------------------------------------------

#: The order Vissim writes top-level containers in, restricted to the ones this
#: emission produces. Source: RT's child order, which is the same relative order
#: in LHT and NOD. Vissim's reader does not appear to require it — but a file
#: whose container order matches its own save is one fewer difference to explain
#: when the next refusal has to be bisected.
#:
#: The three signal containers were placed by the same measurement, not by
#: guessing that the list is alphabetical: in LHT — the only reference that has
#: them — ``detectors`` precedes ``displayTypes``, and ``signalControllers`` and
#: ``signalHeads`` sit between ``netPara`` and ``timeDistributions``, exactly
#: where they appear below. (Both references' restricted orders do happen to be
#: alphabetical, and ``_assert_container_order`` re-checks this list against
#: whatever the emitter produced.)
CONTAINER_ORDER: tuple[str, ...] = (
    "detectors",
    "displayTypes",
    "drivingBehaviors",
    "laneMarkingTypes",
    "levels",
    "linkBehaviorTypes",
    "links",
    "netPara",
    "nodes",
    "signalControllers",
    "signalHeads",
    "timeDistributions",
)
