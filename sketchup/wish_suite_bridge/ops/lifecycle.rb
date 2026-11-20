require_relative '../logger'

module WishSuiteBridge
  module Ops
    module Lifecycle
      # Save the active model. If path is provided, save to that location.
      def self.save(params)
        model = Sketchup.active_model
        path = params["path"]
        begin
          if path && !path.empty?
            result = model.save(path)
          else
            result = model.save
          end
          Logger.info("Model saved: #{model.path}")
          { "status" => result ? "saved" : "save_failed", "path" => model.path }
        rescue => e
          Logger.error("lifecycle.save failed: #{e.message}")
          { "error" => "save failed: #{e.message}" }
        end
      end

      # Save model to a new path (Save As).
      def self.save_as(params)
        path = params["path"]
        return { "error" => "path is required for save_as" } unless path && !path.empty?

        model = Sketchup.active_model
        begin
          result = model.save(path)
          Logger.info("Model saved as: #{path}")
          { "status" => result ? "saved" : "save_failed", "path" => path }
        rescue => e
          Logger.error("lifecycle.save_as failed: #{e.message}")
          { "error" => "save_as failed: #{e.message}" }
        end
      end

      # Create a new empty model.
      # 2026-10-26: no more silent auto-save of the current model (it could
      # write unreviewed AI changes into a real design file). If the current
      # model has unsaved changes the call is refused unless the caller says
      # save_current: true or discard_current: true.
      def self.new_file(params)
        params ||= {}
        guard = guard_unsaved(params, "new_file")
        return guard if guard
        Sketchup.file_new
        Logger.info("New file created")
        { "status" => "new_file_created", "title" => Sketchup.active_model.title }
      end

      # Open an existing .skp file.
      def self.open_file(params)
        path = params["path"]
        return { "error" => "path is required" } unless path && !path.empty?
        return { "error" => "File not found: #{path}" } unless File.exist?(path)

        # If it's already the active model, just return success
        model = Sketchup.active_model
        if model.path && File.expand_path(model.path).casecmp(File.expand_path(path)) == 0
          return { "status" => "already_open", "path" => model.path }
        end

        guard = guard_unsaved(params, "open_file")
        return guard if guard
        Sketchup.open_file(path)
        Logger.info("Opened file: #{path}")
        { "status" => "opened", "path" => path }
      end

      # Close SketchUp gracefully.
      def self.close(params)
        guard = guard_unsaved(params || {}, "close")
        return guard if guard
        Logger.info("Closing SketchUp...")
        # Delay slightly so the response can be sent before closing
        UI.start_timer(0.5, false) { Sketchup.send_action("fixedClose:") }
        { "status" => "closing" }
      end

      # Return information about the current model.
      def self.model_info(_params)
        model = Sketchup.active_model
        {
          "title"       => model.title,
          "path"        => model.path,
          "modified"    => model.modified?,
          "description" => model.description,
          "geo_location" => model.georeferenced? ? {
            "lat" => model.shadow_info["Latitude"],
            "lng" => model.shadow_info["Longitude"]
          } : nil,
          "units" => model.options["UnitsOptions"]["LengthUnit"],
          "entity_count" => model.entities.length,
          "component_definitions" => model.definitions.length,
          "materials_count" => model.materials.length,
          "layers_count" => model.layers.length
        }
      end

      # nil when it is safe to leave the current model; otherwise an error
      # hash that tells the caller what to pass. Never saves silently.
      def self.guard_unsaved(params, action)
        model = Sketchup.active_model
        return nil unless model.modified?
        if params["save_current"] == true
          return { "error" => "Current model is untitled; save it with save_as first, then retry #{action}." } if model.path.to_s.empty?
          model.save
          Logger.info("Saved current model before #{action} (explicitly requested)")
          return nil
        end
        if params["discard_current"] == true
          model.close(true) if model.respond_to?(:close)  # ignore changes, no prompt
          Logger.info("Discarded unsaved changes before #{action} (explicitly requested)")
          return nil
        end
        { "error" => "Current model '#{model.title}' has unsaved changes. Nothing was done. " \
                     "Pass save_current: true to save it first, or discard_current: true to drop the changes.",
          "code" => "unsaved_changes", "title" => model.title, "path" => model.path }
      end
    end
  end
end
