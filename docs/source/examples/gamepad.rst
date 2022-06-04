Gamepad
===============================================================================================

.. literalinclude:: ../../../examples/gamepad.py
   :language: python


Mappings
+++++++++++++++++++++++++++++++++++
For `DualShock 4` and `DualSense` controllers, the appropriate mapping will be set automatically.

Other controllers are supported but will likely need a custom mapping.
This can be done by creating a `.yaml` file and then loading it at runtime.

Gamepad support is provided through `pygame`_.

For more information on mappings see the `pygame docs`_.

DualShock 4 Mapping Example
-----------------------------------
.. literalinclude:: ../../../examples/ds4_mapping.yaml
   :language: yaml

Xbox 360 Mapping Example
-----------------------------------
.. literalinclude:: ../../../examples/x360_mapping.yaml
   :language: yaml
   


.. _pygame: https://www.pygame.org
.. _pygame docs: https://www.pygame.org/docs/ref/joystick.html