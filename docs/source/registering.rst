Registering
===============================================================================================

To get started, you will need to complete the following.

- Retrieving PSN account info.

- Linking PSN account with Remote Play device.

These steps can be accomplished via the CLI or GUI, but this will cover how to programatically complete these steps.

Retrieving PSN account info
+++++++++++++++++++++++++++++++++++++++++++++
This step only has to be done once per PSN user. Once the data is saved you will not have to complete this step again.

::

   from pyremoteplay import RPDevice
   from pyremoteplay import oauth

   # This is the url that users will need to sign in
   # Must be done in a web browser
   url = oauth.get_login_url()

   # User should be redirected to a page that says 'redirect'
   # Have the user supply the url of this page
   account = oauth.get_account_info(redirect_url)

   # Format Account to User Profile
   user_profile = oauth.format_user_account(account)

   # User Profile should be saved for future use
   profiles = RPDevice.get_profiles()
   profiles.update_user(user_profile)
   profiles.save()


Alternatively, you can also use the helper method :func:`pyremoteplay.profile.Profiles.new_user()`

::

   profiles.new_user(redirect_url, save=True)



Linking PSN account with Remote Play device
+++++++++++++++++++++++++++++++++++++++++++++

Now that we have a user profile. We can link the User to a Remote Play device.
Linking needs to be performed once for each device per user.

The PSN User must be logged in on the device.
The user should supply the linking PIN from 'Remote Play' settings on the device.
The pin must be a string.

::

   ip_address = '192.169.0.2'
   device = RPDevice(ip_address)
   device.get_status()  # Device needs a valid status

   device.register(user_profile.name, pin, save=True)
