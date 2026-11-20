require_relative '../logger'

module WishSuiteBridge
  module Ops
    # Material upsert + assign by ai_id. Centralizes material handling so
    # build scripts don't have to call `set_material` with hex strings every
    # time.
    module Material
      # Create or update a material by id.
      # Params:
      #   id          (str)  — material ai_id (also used as SketchUp material name)
      #   color_hex   (str)  — "RRGGBB" without leading #
      #   alpha       (0..1) — optional, default 1.0
      def self.upsert(params)
        if params['skm_path']
          pp=params.merge('path'=>params['skm_path'], 'name'=>params['id'] || params['name'])
          return WishSuiteBridge::Ops::Quality.import_material(pp)
        end
        mat_id = params["id"] || params["ai_id"]
        color_hex = params["color_hex"]
        alpha = (params["alpha"] || 1.0).to_f

        return { "error" => "id is required" } unless mat_id
        return { "error" => "color_hex is required" } unless color_hex

        rgb = parse_hex(color_hex)
        return { "error" => "color_hex must look like #RRGGBB, RRGGBB or #RGB; got #{color_hex.inspect}" } unless rgb
        r, g, b = rgb

        model = Sketchup.active_model
        mat = model.materials[mat_id] || model.materials.add(mat_id)
        mat.color = Sketchup::Color.new(r, g, b)
        mat.alpha = alpha if mat.respond_to?(:alpha=)

        { "id" => mat_id, "status" => "upserted", "rgb" => [r, g, b] }
      end

      # "#EEE9DF", "EEE9DF", "0xEEE9DF", "#EEE" -> [r, g, b]; nil if invalid.
      # (Before 2026-10-22 the leading "#" was not stripped, giving wrong colours.)
      def self.parse_hex(value)
        s = value.to_s.strip.sub(/\A(#|0x)/i, "")
        s = s.chars.map { |c| c * 2 }.join if s.length == 3
        return nil unless s =~ /\A[0-9a-fA-F]{6}\z/
        [s[0..1].to_i(16), s[2..3].to_i(16), s[4..5].to_i(16)]
      end

      # Assign a material to a list of entity ai_ids.
      def self.assign(params)
        if params['target_paths']
          return WishSuiteBridge::Ops::Quality.paint(params.merge('paths'=>params['target_paths'], 'material'=>params['material_id']))
        end
        target_ids = params["target_ids"] || []
        mat_id = params["material_id"]
        return { "error" => "material_id is required" } unless mat_id

        model = Sketchup.active_model
        mat = model.materials[mat_id]
        return { "error" => "Material not found: #{mat_id}" } unless mat

        applied = []
        missing = []
        target_ids.each do |ai_id|
          ent = WishSuiteBridge::AI_ID.find(ai_id)
          if ent
            ent.material = mat
            applied << ai_id
          else
            missing << ai_id
          end
        end
        { "applied" => applied, "missing" => missing, "status" => "ok" }
      end
    end

    # Layer (Tag) upsert + assign.
    module Layer
      def self.upsert(params)
        name = params["id"] || params["name"]
        return { "error" => "id is required" } unless name
        model = Sketchup.active_model
        tag = model.layers[name] || model.layers.add(name)
        if params["color"]
          c = params["color"]
          tag.color = Sketchup::Color.new(c[0].to_i, c[1].to_i, c[2].to_i)
        end
        tag.visible = params["visible"] != false
        { "id" => name, "status" => "upserted" }
      end

      def self.assign(params)
        target_ids = params["target_ids"] || []
        layer_id = params["layer_id"]
        return { "error" => "layer_id is required" } unless layer_id

        model = Sketchup.active_model
        tag = model.layers[layer_id]
        return { "error" => "Layer not found: #{layer_id}" } unless tag

        applied = []
        target_ids.each do |ai_id|
          ent = WishSuiteBridge::AI_ID.find(ai_id)
          if ent
            ent.layer = tag
            applied << ai_id
          end
        end
        { "applied" => applied, "status" => "ok" }
      end
    end
  end
end
