"""Public reference-data lookups and server-side Link hierarchy validation only."""
import frappe
from comfort_homes.hp_lending import hp_settings as s


@frappe.whitelist(allow_guest=True)
def locations(level="Province", parent=None):
    # Explicit master-only allowlist, fixed country/fields. Never a generic guest query.
    config={"Province":("province",None),"City":("city_name","province"),"Suburb":("suburb_name","city")}
    if level not in config: frappe.throw("Unsupported location level")
    label,field=config[level]
    if field and not parent: return []
    filters={"country":"Fiji"}
    if level=="Province": filters["is_active"]=1
    if field: filters[field]=parent
    return [dict(value=r.name,label=r.get(label)) for r in frappe.get_all(level,filters=filters,fields=["name",label],order_by=label+" asc",limit_page_length=1000)]


def validate_locations(doc, method=None):
    if doc.get("company")!=s.COMPANY: return
    province,city,suburb=(doc.get(k) for k in ("custom_province","custom_new_city","custom_suburb"))
    if province:
        row=frappe.db.get_value("Province",province,["province","country","is_active"],as_dict=True)
        if not row or row.country!="Fiji" or not row.is_active: frappe.throw("Select an active Fiji Province from the list")
        doc.state=row.province
    if city:
        row=frappe.db.get_value("City",city,["city_name","province","country"],as_dict=True)
        if not row or row.province!=province or row.country!="Fiji": frappe.throw("City must belong to the selected Province")
        doc.city=row.city_name
    if suburb:
        row=frappe.db.get_value("Suburb",suburb,["city","province","country"],as_dict=True)
        if not row or row.city!=city or row.province!=province or row.country!="Fiji": frappe.throw("Suburb must belong to the selected City and Province")
