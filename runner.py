import bot
import iptv_extension
import broadcast_admin

# bot imports and installs storefront first; load extensions afterwards so
# their IPTV/admin overrides are applied to the active bot namespace.
iptv_extension.s.install(bot.__dict__)
broadcast_admin.install(bot.__dict__)
bot.main()
