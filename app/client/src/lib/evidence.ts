/**
 * Copy for the cockpit's deterministic-evidence tooltips. Each string explains, in
 * plain terms, HOW that authority computes its result — not what this particular claim
 * returned. The wording mirrors the deterministic authorities the agent runs
 * (agent/src/authorities.py) so an adjuster can see the basis of each supporting source
 * before accepting or overriding a recommendation.
 */

export const EVIDENCE_APPROACH = {
  conformance:
    'Compares the coil’s Mill Test Certificate measured properties against the governing specification’s required property bands. Conforms only when every measured property falls within its allowed range; any property outside its band is listed as non-conforming.',
  coverage:
    'Applies the warranty terms to the claim: measures elapsed time against the coverage window, evaluates the listed exclusions, and derives a proration factor from the elapsed months. Covered when the claim is inside the window with no exclusion hit.',
  duplicate:
    'Screens the claim against prior claims on the same coil and defect using narrative similarity across a candidate set. Flags a duplicate when similarity to an existing claim exceeds the decision threshold.',
  settlement:
    'Derives the approved amount from covered tonnage (capped at shipped tonnage) at the settlement rate, plus freight when freight is covered. Flags an over-claim when the claimed amount exceeds the covered entitlement; a partial settlement pays only the covered portion.',
} as const;

export type EvidenceKey = keyof typeof EVIDENCE_APPROACH;
