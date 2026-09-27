CREATE OR REPLACE VIEW `fe-bar-ir`.gold.quality_claims_metrics
WITH METRICS
LANGUAGE YAML
AS $$
  version: 1.1
  source: fe-bar-ir.gold.gold_claim_adjudication_fact
  comment: "Governed quality-claim outcome and value measures for Genie and AI/BI dashboards. Value categories remain separate and auditable."
  dimensions:
    - name: Claim Date
      expr: claim_date
      comment: "Calendar date the claim was submitted."
      display_name: "Claim Date"
    - name: Claim Type
      expr: claim_type
      comment: "Business claim category."
      display_name: "Claim Type"
    - name: Region
      expr: region
      comment: "Commercial region associated with the claimed coil."
      display_name: "Region"
    - name: Product
      expr: product
      comment: "Steel product line."
      display_name: "Product"
    - name: Grade
      expr: grade
      comment: "Steel grade."
      display_name: "Grade"
    - name: Coating
      expr: coating
      comment: "Coating class."
      display_name: "Coating"
    - name: Verdict
      expr: verdict
      comment: "Final adjudication verdict: APPROVE, DENY, or PEND."
      display_name: "Verdict"
    - name: Disposition
      expr: disposition
      comment: "Final business disposition."
      display_name: "Disposition"
    - name: Customer
      expr: customer_name
      comment: "Customer associated with the claim."
      display_name: "Customer"
    - name: Supplier
      expr: recovery_supplier_name
      comment: "Supplier identified for recovery when supplier-attributable."
      display_name: "Recovery Supplier"
  measures:
    - name: Claim Count
      expr: COUNT(1)
      comment: "Number of adjudication-grain claim outcomes."
      display_name: "Claim Count"
    - name: Approved Claims
      expr: "COUNT(1) FILTER (WHERE verdict = 'APPROVE')"
      comment: "Number of approved claim outcomes."
      display_name: "Approved Claims"
    - name: Denied Claims
      expr: "COUNT(1) FILTER (WHERE verdict = 'DENY')"
      comment: "Number of denied claim outcomes."
      display_name: "Denied Claims"
    - name: Pending Claims
      expr: "COUNT(1) FILTER (WHERE verdict = 'PEND')"
      comment: "Number of pending claim outcomes."
      display_name: "Pending Claims"
    - name: Approval Rate
      expr: "MEASURE(`Approved Claims`) / MEASURE(`Claim Count`)"
      comment: "Share of claim outcomes approved."
      display_name: "Approval Rate"
      format:
        type: percentage
    - name: Denial Rate
      expr: "MEASURE(`Denied Claims`) / MEASURE(`Claim Count`)"
      comment: "Share of claim outcomes denied."
      display_name: "Denial Rate"
      format:
        type: percentage
    - name: Pend Rate
      expr: "MEASURE(`Pending Claims`) / MEASURE(`Claim Count`)"
      comment: "Share of claim outcomes pending investigation."
      display_name: "Pend Rate"
      format:
        type: percentage
    - name: Claimed Amount
      expr: SUM(claimed_amount)
      comment: "Total claimed amount in the dataset currency."
      display_name: "Claimed Amount"
    - name: Approved Amount
      expr: SUM(approved_amount)
      comment: "Total approved amount in the dataset currency."
      display_name: "Approved Amount"
    - name: Blocked Amount
      expr: "SUM(claimed_amount) FILTER (WHERE verdict = 'DENY')"
      comment: "Claimed amount blocked by final denial, including the separately governed categories."
      display_name: "Blocked Amount"
    - name: In Spec Denial Amount
      expr: "SUM(claimed_amount) FILTER (WHERE verdict = 'DENY' AND claim_type = 'material_nonconformance' AND COALESCE(material_in_spec, true))"
      comment: "Claimed amount denied for material claims assessed as in specification."
      display_name: "In-Spec Denial Amount"
    - name: Warranty Exclusion Denial Amount
      expr: "SUM(claimed_amount) FILTER (WHERE verdict = 'DENY' AND claim_type = 'coating_warranty' AND NOT COALESCE(warranty_covered, false))"
      comment: "Claimed amount denied because warranty coverage or exclusions did not permit payment."
      display_name: "Warranty Exclusion Denial Amount"
    - name: Duplicate Blocked Amount
      expr: "SUM(claimed_amount) FILTER (WHERE duplicate_flag)"
      comment: "Claimed amount blocked as duplicate."
      display_name: "Duplicate Blocked Amount"
    - name: Over Claim Reduction Amount
      expr: "SUM(CASE WHEN verdict = 'APPROVE' AND approved_amount < claimed_amount THEN claimed_amount - approved_amount ELSE 0 END)"
      comment: "Reduction between claimed and approved amounts on partially approved over-claims."
      display_name: "Over-Claim Reduction Amount"
$$;
