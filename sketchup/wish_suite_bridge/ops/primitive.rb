module WishSuiteBridge
  module Ops
    module PrimitiveOps
      def self.create(params)
        model = Sketchup.active_model
        ents  = model.active_entities

        q=WishSuiteBridge::Ops::Quality;ents,parent_world=q.model_context(params)
        ai_id=params['ai_id'] || params['id'] || WishSuiteBridge::AI_ID.auto_name(params['kind'] || :primitive)
        kind=params['kind'];dims=params['dimensions'] || {};transform=params['transform'] || {}
        raise "Unknown primitive kind: #{kind}" unless %w[box cylinder sphere cone pyramid text wall_with_opening].include?(kind)
        dims.each{|k,v|if k.end_with?('_mm');x=Float(v);raise "Invalid positive dimension: #{k}" unless x.finite? && x>0;end}
        n=Integer(dims.fetch('segments',24));raise 'segments must be8..256' unless (8..256).cover?(n)
        placement=q.placement(params,parent_world);digest=q.spec_digest(params);existing=q.existing_in(ai_id,ents)
        return {'ai_id'=>ai_id,'guid'=>existing.guid,'status'=>'unchanged','rebuilt'=>false} if existing && existing.get_attribute('su_mcp_bridge','build_spec_sha256')==digest
        q.with_operation('Create or update primitive '+ai_id) do
        group = ents.add_group
        ge = group.entities

        case kind
        when "box"
          w = (dims["width_mm"] || 1000).to_f / 25.4
          d = (dims["depth_mm"] || 1000).to_f / 25.4
          h = (dims["height_mm"] || 1000).to_f / 25.4
          face = ge.add_face(
            Geom::Point3d.new(0, 0, 0),
            Geom::Point3d.new(w, 0, 0),
            Geom::Point3d.new(w, d, 0),
            Geom::Point3d.new(0, d, 0)
          )
          face.reverse! if face.normal.z < 0
          face.pushpull(h)

        when "cylinder"
          r = (dims["radius_mm"] || 500).to_f / 25.4
          h = (dims["height_mm"] || 1000).to_f / 25.4
          n = (dims["segments"] || 24).to_i
          center = Geom::Point3d.new(0, 0, 0)
          normal = Geom::Vector3d.new(0, 0, 1)
          circle = ge.add_circle(center, normal, r, n)
          face = ge.add_face(circle)
          face.reverse! if face.normal.z < 0
          face.pushpull(h)

        when "sphere"
          r = (dims["radius_mm"] || 500).to_f / 25.4
          n = (dims["segments"] || 24).to_i
          # Build sphere via half-circle revolution
          center = Geom::Point3d.new(0, 0, 0)
          # Create a half-circle profile path
          pts = []
          (0..n).each do |i|
            angle = Math::PI * i / n
            pts << Geom::Point3d.new(r * Math.cos(angle), 0, r * Math.sin(angle))
          end
          # Add path edges
          path_edges = []
          (0...pts.length-1).each do |i|
            path_edges << ge.add_line(pts[i], pts[i+1])
          end
          # Create circle for follow-me
          circle = ge.add_circle(Geom::Point3d.new(r, 0, 0), Geom::Vector3d.new(0, 0, 1), r, n)
          face = ge.add_face(circle)
          # Use follow_me with the half-circle path
          face.followme(path_edges)

        when "cone"
          r = (dims["radius_mm"] || 500).to_f / 25.4
          h = (dims["height_mm"] || 1000).to_f / 25.4
          n = (dims["segments"] || 24).to_i
          # Create base circle
          center = Geom::Point3d.new(0, 0, 0)
          normal = Geom::Vector3d.new(0, 0, 1)
          circle_edges = ge.add_circle(center, normal, r, n)
          # Connect each edge vertex to the apex
          apex = Geom::Point3d.new(0, 0, h)
          circle_edges.each do |edge|
            ge.add_line(edge.start.position, apex)
          end
          # Add base face
          ge.add_face(circle_edges)

        when "pyramid"
          w = (dims["width_mm"] || 1000).to_f / 25.4
          d = (dims["depth_mm"] || 1000).to_f / 25.4
          h = (dims["height_mm"] || 1000).to_f / 25.4
          hw, hd = w/2.0, d/2.0
          apex = Geom::Point3d.new(0, 0, h)
          p1 = Geom::Point3d.new(-hw, -hd, 0)
          p2 = Geom::Point3d.new( hw, -hd, 0)
          p3 = Geom::Point3d.new( hw,  hd, 0)
          p4 = Geom::Point3d.new(-hw,  hd, 0)
          ge.add_face(p1, p2, p3, p4)  # base
          ge.add_face(p1, p2, apex)
          ge.add_face(p2, p3, apex)
          ge.add_face(p3, p4, apex)
          ge.add_face(p4, p1, apex)
        when "wall_with_opening"
          w=Float(dims.fetch('width_mm',5000));d=Float(dims.fetch('depth_mm',200));h=Float(dims.fetch('height_mm',3000));o=params.fetch('opening');x,z,ow,oh=%w[x_mm z_mm width_mm height_mm].map{|k|Float(o.fetch(k))}
          raise 'Opening must be finite and strictly inside the wall' unless [x,z,ow,oh].all?(&:finite?) && x>0 && z>0 && ow>0 && oh>0 && x+ow<w && z+oh<h
          outer=[[0,0,0],[w,0,0],[w,0,h],[0,0,h]].map{|a|Geom::Point3d.new(*a.map{|v|v.mm})};ge.add_face(outer)
          hole=ge.add_face([[x,0,z],[x+ow,0,z],[x+ow,0,z+oh],[x,0,z+oh]].map{|a|Geom::Point3d.new(*a.map{|v|v.mm})});hole.erase!
          profile=ge.grep(Sketchup::Face).max_by(&:area);profile.reverse! if profile.normal.y<0;profile.pushpull(d.mm)
          raise 'Opening wall is not a closed solid' unless group.manifold?
        when "text"
          text = params["text"] || "Text"
          h = (dims["height_mm"] || 1000).to_f / 25.4
          ge.add_3d_text(text, TextAlignLeft, "Arial", true, false, h, 0.0, 0.5, true, 5.0)

        else
          return { "error" => "Unknown primitive kind: #{kind}" }
        end

        group.transform!(placement)
        existing.erase! if existing
        q.built(group,ai_id,digest,params)
        { "ai_id" => ai_id, "guid" => group.guid, "kind" => kind, "status" => existing ? "updated" : "created", "rebuilt"=>true }
        end
      end
    end
  end
end
