from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("projects.project", "projects"),
    *crud("projects.issue", "material issues to site"),
    PermissionDef("projects.issue.post", "Post material issues (stock out to site)"),
]
