import bot
import iptv_extension
import broadcast_admin
import chatgpt_extension

# bot.py already installs storefront and all extensions during import.
# Do not reinstall storefront here: doing so overwrites the IPTV/ChatGPT
# category wrappers and makes those buttons fall back to Grok rendering.
broadcast_admin.install(bot.__dict__)
chatgpt_extension.install(bot.__dict__)

# storefront.action resolves admin_panel from storefront globals, so point
# that global at the admin-only broadcast panel as well.
iptv_extension.s.admin_panel = bot.show_home.__globals__.get('admin_panel', iptv_extension.s.admin_panel)
# broadcast_admin.install replaces the active action; expose its admin panel
# through storefront globals by extracting it from the action closure.
for cell in (bot.action.__closure__ or ()):
    obj = cell.cell_contents
    if callable(obj) and getattr(obj, '__name__', '') == 'admin_panel':
        iptv_extension.s.admin_panel = obj
        break

bot.main()
