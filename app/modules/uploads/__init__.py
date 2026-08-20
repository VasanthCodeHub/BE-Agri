"""Upload authorisation — one endpoint, one responsibility.

The app uploads photo bytes DIRECTLY to Cloudinary; this module issues the
signature that makes that upload possible and confines it to our folder. See
app/integrations/cloudinary.py for why signing beats an unsigned preset.
"""
