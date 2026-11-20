require 'sketchup.rb'
require 'extensions.rb'

unless file_loaded?(__FILE__)
  extension = SketchupExtension.new('Wish Suite Bridge', 'wish_suite_bridge/main')
  extension.description = 'Local authenticated bridge for the Wish SU/CAD/PS workflow.'
  extension.version = '0.2.0'
  extension.creator = 'Wish Suite contributors'
  Sketchup.register_extension(extension, true)
  file_loaded(__FILE__)
end
