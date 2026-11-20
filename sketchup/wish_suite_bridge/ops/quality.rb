# encoding: utf-8
require 'json'
require 'fileutils'
require 'digest'
require 'securerandom'
require 'time'
module WishSuiteBridge
 module Ops
  module Quality
   VERSION='1.4.1'
   MM=25.4

   # Millimetres, local axes and stable IDs shared by the supported modeling operations.
   def self.canonical(v)
    case v;when Hash;v.keys.sort.each_with_object({}){|k,h|h[k]=canonical(v[k])};when Array;v.map{|x|canonical(x)};else;v;end
   end
   def self.spec_digest(v);Digest::SHA256.hexdigest(JSON.generate(canonical(v)));end
   def self.numbers(a,n,label)
    raise "#{label} must contain#{n} numbers" unless a.is_a?(Array) && a.size==n
    a.map{|v|x=Float(v);raise "#{label} must be finite" unless x.finite?;x}
   end
   def self.model_context(p)
    m=Sketchup.active_model;ids=p['parent_path']
    if ids && !ids.empty?
     ids.each_index{|i|ent,_=find_path(ids[0..i]);raise 'Shared parent definition: make the selected branch unique first' if ent.definition.instances.size>1}
     ent,tr=find_path(ids);[entities(ent),tr]
    elsif ids
     [m.entities,Geom::Transformation.new]
    else
     raise 'Shared edit context: make unique first' if (m.active_path || []).any?{|ent|ent.definition.instances.size>1}
     [m.active_entities,m.edit_transform]
    end
   end
   def self.placement(p,parent_world)
    a=p['transform'] || {};pos=numbers(a['position_mm'] || p['position_mm'] || [0,0,0],3,'position_mm')
    rr=a['rotation_deg'] || p['rotation_deg'] || [0,0,0];rr=[0,0,rr] unless rr.is_a?(Array);rr=numbers(rr,3,'rotation_deg')
    ss=numbers(a['scale'] || p['scale'] || [1,1,1],3,'scale');raise 'Scale must be positive' unless ss.all?{|x|x>0}
    tr=Geom::Transformation.translation(pos.map{|x|x.mm})
    [[Z_AXIS,rr[2]],[Y_AXIS,rr[1]],[X_AXIS,rr[0]]].each{|axis,r|tr=tr*Geom::Transformation.rotation(ORIGIN,axis,r.degrees) unless r==0}
    tr=tr*Geom::Transformation.scaling(*ss)
    space=p.fetch('coordinate_space','local');raise 'coordinate_space must be local or world' unless ['local','world'].include?(space)
    space=='world' ? parent_world.inverse*tr : tr
   end
   def self.existing_in(id,es)
    return nil unless id
    e=WishSuiteBridge::AI_ID.find(id);raise 'Stable ID exists in another parent; supply its correct parent_path' if e && !es.to_a.include?(e);e
   end
   def self.built(e,id,digest,p)
    WishSuiteBridge::AI_ID.set(e,id,tags:p['tags'] || []);e.set_attribute('su_mcp_bridge','build_spec_sha256',digest);e
   end
   def self.vec(p);p.to_a.map{|v|(v*MM).round(4)};end
   def self.entities(i);i.is_a?(Sketchup::Group) ? i.entities : i.definition.entities;end
   def self.instance?(e);e.is_a?(Sketchup::Group) || e.is_a?(Sketchup::ComponentInstance);end
   def self.walk(es=Sketchup.active_model.entities,tr=Geom::Transformation.new,path=[],anc=[],visible=true,&block)
    es.each do |e|
     next unless e.valid?
     vis=visible && !e.hidden? && e.layer.visible?
     if instance?(e)
      tt=tr*e.transformation;pp=path+[e.persistent_id];yield(e,tt,pp,vis)
      next if anc.include?(e.definition.object_id) || path.length>=24
      walk(entities(e),tt,pp,anc+[e.definition.object_id],vis,&block)
     end
    end
   end
   def self.rows
    a=[];walk{|e,t,p,v|a<<[e,t,p,v]};a
   end
   def self.find_path(ids)
    es=Sketchup.active_model.entities;t=Geom::Transformation.new;e=nil
    ids.each{|id|e=es.find{|x|instance?(x) && x.persistent_id==id.to_i};raise "Instance path not found: #{ids}" unless e;t=t*e.transformation;es=entities(e)}
    [e,t]
   end
   def self.bb(e,t)
    b=Geom::BoundingBox.new;local=e.definition.bounds;8.times{|i|b.add(local.corner(i).transform(t))};b
   end
   def self.bounds_hash(b)
    return nil unless b.valid?
    {min_mm:vec(b.min),max_mm:vec(b.max),size_xyz_mm:[b.width,b.height,b.depth].map{|x|(x*MM).round(3)}}
   end
   def self.camera
    c=Sketchup.active_model.active_view.camera
    {eye_mm:vec(c.eye),target_mm:vec(c.target),up:c.up.to_a,perspective:c.perspective?,fov_deg:c.fov,height_mm:c.height*MM,aspect_ratio:c.aspect_ratio}
   end
   def self.set_camera(p)
    v=Sketchup.active_model.active_view
    c=Sketchup::Camera.new(p.fetch('eye_mm').map{|x|x.to_f.mm},p.fetch('target_mm').map{|x|x.to_f.mm},p['up'] || [0,0,1])
    c.perspective=p.fetch('perspective',false)
    c.perspective? ? c.fov=(p['fov_deg'] || 35).to_f : c.height=p.fetch('height_mm',30000).to_f.mm
    c.aspect_ratio=p['aspect_ratio'].to_f if p['aspect_ratio'].to_f>0
    v.camera=c;v.refresh;camera
   end
   def self.material_info(m)
    t=m.texture
    {name:m.name,alpha:m.alpha,texture:!!t,texture_filename:t ? t.filename : nil,texture_source_exists:t ? File.file?(t.filename) : nil,embedded_texture:!!t,texture_size_mm:t ? [t.width*MM,t.height*MM] : nil,pixels:t ? [t.image_width,t.image_height] : nil,source_skm:m.get_attribute('SAIEQuality','source_skm'),source_sha256:m.get_attribute('SAIEQuality','source_sha256')}
   end
   def self.inventory(p={})
    m=Sketchup.active_model;a=rows
    out={plugin_version:VERSION,sketchup:Sketchup.version,path:m.path,modified:m.modified?,camera:camera,render:{mode:m.rendering_options['RenderMode'],texture:m.rendering_options['Texture'],color_by_tag:m.rendering_options['DisplayColorByLayer'],transparency:m.rendering_options['MaterialTransparency']},materials:m.materials.map{|mat|material_info(mat)},instances:a.map{|e,t,ip,v|{path:ip,name:e.name,kind:e.typename,visible:v,definition:e.definition.name,world_transform:t.to_a,world_bounds:bounds_hash(bb(e,t)),direct_faces:entities(e).grep(Sketchup::Face).length,manifold:e.manifold?,material:e.material&.name}}}
    write_json(p['output'],out) if p['output'];out
   end
   def self.write_json(path,data)
    FileUtils.mkdir_p(File.dirname(path));File.write(path,JSON.pretty_generate(data));path
   end
   def self.capture(p)
    capture_clock=Process.clock_gettime(Process::CLOCK_MONOTONIC)
    set_camera(p['camera']) if p['camera']
    file=File.expand_path(p.fetch('path'));FileUtils.mkdir_p(File.dirname(file));v=Sketchup.active_model.active_view;v.refresh
    if p['scene_name']
     page=Sketchup.active_model.pages[p['scene_name']] || Sketchup.active_model.pages.add(p['scene_name']);page.use_camera=true;page.update
    end
    ok=v.write_image(filename:file,width:p.fetch('width',1800).to_i,height:p.fetch('height',1250).to_i,antialias:true)
    raise 'SketchUp did not write image' unless ok && File.file?(file) && File.size(file)>100
    result={status:'captured_actual_sketchup_viewport',path:file,bytes:File.size(file),sha256:Digest::SHA256.file(file).hexdigest,camera:camera,model_path:Sketchup.active_model.path,model_modified:Sketchup.active_model.modified?,time:Time.now.iso8601}
    result[:render]={mode:Sketchup.active_model.rendering_options['RenderMode'],texture:Sketchup.active_model.rendering_options['Texture'],shadows:Sketchup.active_model.shadow_info['DisplayShadows'],section_cuts:Sketchup.active_model.rendering_options['DisplaySectionCuts']}
    result[:capture_seconds]=Process.clock_gettime(Process::CLOCK_MONOTONIC)-capture_clock
    write_json(file+'.json',result);result
   end
   def self.with_operation(name)
    raise 'A quality operation or native save is pending' if @busy && !@in_job
    return yield if @in_job || @operation_depth.to_i>0
    m=Sketchup.active_model;m.start_operation(name,true);@operation_depth=1
    begin;r=yield;m.commit_operation;r
    rescue Exception;m.abort_operation;WishSuiteBridge::AI_ID.reset;raise
    ensure;@operation_depth=0;end
   end
   def self.import_material(p);with_operation('SAIE import native texture'){import_material_impl(p)};end
   def self.paint(p);with_operation('SAIE paint actual faces'){paint_impl(p)};end
   def self.import_material_impl(p)
    path=File.expand_path(p.fetch('path'));raise "SKM not found: #{path}" unless File.file?(path)
    raise 'Only native SKM is supported by this endpoint' unless File.extname(path).downcase=='.skm'
    digest=Digest::SHA256.file(path).hexdigest
    mat=p['name'] && Sketchup.active_model.materials[p['name']]
    if mat
     raise 'Existing material name belongs to a different source SKM' unless mat.get_attribute('SAIEQuality','source_sha256')==digest
    else
     mat=Sketchup.active_model.materials.load(path);raise 'SKM load failed' unless mat
     mat.name=p['name'] if p['name']
    end
    raise 'SKM contains no bitmap texture' if p.fetch('require_texture',true) && !mat.texture
    if mat.texture && p['width_mm']
     ratio=mat.texture.height/mat.texture.width;w=p['width_mm'].to_f;raise 'Invalid texture width' unless w>0;mat.texture.size=[w.mm,(p['height_mm'] || w*ratio).to_f.mm]
    end
    mat.set_attribute('SAIEQuality','source_skm',path);mat.set_attribute('SAIEQuality','source_sha256',Digest::SHA256.file(path).hexdigest)
    if mat.texture && p['export_texture']
     file=p['export_texture'];FileUtils.mkdir_p(File.dirname(file));raise 'Texture extraction failed' unless mat.texture.write(file)
     mat.texture=file # Explicit portable source, embedded by SketchUp on save.
     w=p['width_mm'] || 1000;mat.texture.size=[w.to_f.mm,(p['height_mm'] || w).to_f.mm]
     mat.set_attribute('SAIEQuality','portable_texture',file)
    end
    material_info(mat)
   end
   def self.paint_impl(p)
    mat=Sketchup.active_model.materials[p.fetch('material')];raise 'Material missing' unless mat;raise 'Texture required' if p['require_texture'] && !mat.texture
    list=p.fetch('paths');count=0;seen={}
    list.each do |ip|
     ent,world=find_path(ip);ent.make_unique if p.fetch('make_unique',true)
     apply=lambda do |obj,tr|
      obj.material=nil unless p['face_ids']
      entities(obj).each do |e|
       if e.is_a?(Sketchup::Face)
        next if p['face_ids'] && !p['face_ids'].include?(e.persistent_id)
        e.material=mat;e.back_material=mat if p.fetch('both_sides',true)
        if mat.texture
         # Real world planar UV projection. Align U with horizontal intersection of the face plane.
         pts=e.vertices.map{|v|v.position.transform(tr)};origin=pts.first
         axes=e.normal.axes;n=axes[0].transform(tr).cross(axes[1].transform(tr));raise 'Degenerate instance transform' if n.length.to_f<1.0e-8;n.normalize!
         u=n.cross([0,0,1]);u=Geom::Vector3d.new(1,0,0) if u.length.to_f<1.0e-5;u.normalize!;u.reverse! if u.x< -0.01 || (u.x.abs<0.01 && u.y<0)
         vv=n.cross(u);vv.normalize!;inv=tr.inverse
         w=mat.texture.width;h=mat.texture.height
         begin
          wp=[origin,origin.offset(u,w),origin.offset(vv,h)]
         rescue=>ex
          raise "UV basis failed face=#{e.persistent_id} n=#{n.to_a} u=#{u.to_a} v=#{vv.to_a} width=#{w} height=#{h} mat=#{mat.name}: #{ex.message}"
         end
         mapping=[wp[0].transform(inv),Geom::Point3d.new(0,0,1),wp[1].transform(inv),Geom::Point3d.new(1,0,1),wp[2].transform(inv),Geom::Point3d.new(0,1,1)]
         e.position_material(mat,mapping,true);e.position_material(mat,mapping,false) if p.fetch('both_sides',true)
        end
        count+=1
       elsif instance?(e)
        e.make_unique if p.fetch('make_unique',true);apply.call(e,tr*e.transformation)
       end
      end
     end
     apply.call(ent,world)
    end
    {status:'assigned_to_actual_faces',face_count:count,material:material_info(mat)}
   end
   def self.material_audit(p={})
    m=Sketchup.active_model;stats={};defaults=0;back_only=0;duplicates={};dups=[];writer=Sketchup.create_texture_writer
    scan=lambda do |es,tr,path,inherited,vis|
     es.each do |e|
      next unless e.valid?
      visible=vis && !e.hidden? && e.layer.visible?
      if instance?(e)
       scan.call(entities(e),tr*e.transformation,path+[e.persistent_id],e.material || inherited,visible)
      elsif e.is_a?(Sketchup::Face) && visible
       next if p['paths'] && !p['paths'].any?{|selected|path[0,selected.length]==selected}
       front=e.material || inherited;back=e.back_material || inherited
       defaults+=1 unless front;back_only+=1 if !front && back
       key=front ? front.name : '(default)';r=(stats[key]||={faces:0,direct:0,inherited:0,textured:0,back_mismatch:0,uv_errors:0,texture_scale_checked:0,texture_scale_errors:0,texture_scale_unmeasured:0})
       r[:faces]+=1;e.material ? r[:direct]+=1 : r[:inherited]+=1;r[:textured]+=1 if front && front.texture;r[:back_mismatch]+=1 if front!=back
       if front && front.texture
        begin
         helper=e.get_UVHelper(true,true,writer);samples=e.vertices.map{|v|pt=v.position;uv=helper.get_front_UVQ(pt);raise 'Non-finite UVQ' unless uv.to_a.all?(&:finite?) && uv.z.abs>1e-12;[pt.transform(tr),uv.x/uv.z,uv.y/uv.z]}
         a=samples.first;basis=nil
         samples.drop(1).each_cons(2){|b,c|du=b[1]-a[1];dv=b[2]-a[2];eu=c[1]-a[1];ev=c[2]-a[2];det=du*ev-eu*dv;if det.abs>1e-10 && (!basis || det.abs>basis.last.abs);basis=[b,c,du,dv,eu,ev,det];end}
         if basis
          b,c,du,dv,eu,ev,det=basis;v1=b[0]-a[0];v2=c[0]-a[0]
          uvec=Geom::Vector3d.new(*(0..2).map{|i|(v1[i]*ev-v2[i]*dv)/det});vvec=Geom::Vector3d.new(*(0..2).map{|i|(v2[i]*du-v1[i]*eu)/det})
          measured=[uvec.length.to_f*MM,vvec.length.to_f*MM];expected=[front.texture.width.to_f*MM,front.texture.height.to_f*MM];r[:texture_scale_checked]+=1
          if (0..1).any?{|i|(measured[i]/expected[i]-1).abs>0.05}
           r[:texture_scale_errors]+=1;(r[:scale_examples]||=[])<<{face_path:path+[e.persistent_id],actual_mm:measured,expected_mm:expected} if (r[:scale_examples]||=[]).length<3
          end
         else;r[:texture_scale_unmeasured]+=1;end
        rescue=>ex;r[:uv_errors]+=1;(r[:uv_error_examples]||=[])<<ex.message if (r[:uv_error_examples]||=[]).length<3;end
       end
       if p['duplicates']
        sig=e.vertices.map{|v|v.position.transform(tr).to_a.map{|x|(x*254).round}}.sort
        na=e.normal.axes;world_normal=na[0].transform(tr).cross(na[1].transform(tr));world_normal.normalize!
        if duplicates[sig]
         prev=duplicates[sig];opposed=world_normal.dot(prev[:normal])< -0.99
         dups<<{a:prev[:path],b:path+[e.persistent_id],classification:opposed ? 'opposed_faces_possible_intended_joint' : 'same_facing_duplicate_review_z_fighting'}
        else
         duplicates[sig]={path:path+[e.persistent_id],normal:world_normal}
        end
       end
      end
     end
    end
    scan.call(m.entities,Geom::Transformation.new,[],nil,true)
    out={materials:stats,default_front_faces:defaults,back_only_faces:back_only,exact_duplicate_face_pairs:dups,duplicate_tolerance_mm:0.1,limits:'Exact duplicate polygons only; partial coplanar overlap and texture aesthetics require visual review.'};write_json(p['output'],out) if p['output'];out
   end
   def self.clashes(p={})
    raise 'Cannot run temporary geometry tests while another quality operation/save is pending' if @busy && !@in_clash_tick
    all=rows.select{|e,t,ip,v|(v || p.fetch('include_hidden',true)) && entities(e).grep(Sketchup::Face).any?}
    chosen=p['paths'];all.select!{|e,t,ip,v|chosen.any?{|ids|ip[0,ids.length]==ids}} if chosen
    tol=p.fetch('tolerance_mm',1).to_f/MM;ignore=p.fetch('ignore_pairs',[]).map{|pair|pair.map{|path|path.join('/')}.sort}
    only=(p['only_pairs'] || []).map{|pair|pair.map{|ids|ids.join('/')}.sort};only=only.each_with_object({}){|key,h|h[key]=true}
    candidates=[]
    boxes=all.map{|r|bb(r[0],r[1])}
    all.each_with_index do |a,i|
     (i+1...all.length).each do |j|
      b=all[j];key=[a[2].join('/'),b[2].join('/')].sort;next if !only.empty? && !only[key];next if ignore.include?(key)
      next if a[2][0,b[2].length]==b[2] || b[2][0,a[2].length]==a[2]
      ba=boxes[i];bc=boxes[j];ov=(0..2).map{|k|[ba.max[k],bc.max[k]].min-[ba.min[k],bc.min[k]].max}
      next unless ov.all?{|x|x>tol}
      candidates<<{a:a[2],b:b[2],name_a:a[0].name,name_b:b[0].name,overlap_xyz_mm:ov.map{|x|x*MM},solid_a:a[0].manifold?,solid_b:b[0].manifold?,narrow:'not_tested'}
     end
    end
    tested=0;failures=[];m=Sketchup.active_model
    if p['exact']
     m.start_operation('SAIE quality temporary intersection test',true)
     begin
      candidates.select{|r|r[:solid_a] && r[:solid_b]}.first(p.fetch('max_exact',30).to_i).each do |r|
       created=[]
       begin
        copies=[:a,:b].map do |k|
         ent,tr=find_path(r[k]);g=m.entities.add_group;created<<g;inst=g.entities.add_instance(ent.definition,tr);inst.explode;g
        end
        result=copies[0].intersect(copies[1]);created<<result if result
        if result && result.valid? && result.manifold?
         volume=result.volume.abs*(MM**3);r[:intersection_mm3]=volume;r[:narrow]=volume>p.fetch('min_volume_mm3',1).to_f ? 'confirmed_solid_overlap_review_intent' : 'no_solid_volume_overlap'
        elsif result && result.valid? && entities(result).grep(Sketchup::Face).empty?
         r[:intersection_mm3]=0;r[:narrow]='empty_native_intersection'
        else
         r[:narrow]='kernel_no_closed_result_unverified';failures<<{a:r[:a],b:r[:b],reason:'Nil or nonclosed native intersection; not treated as proof of clearance.'}
        end
        tested+=1
       rescue=>e
        r[:narrow]='kernel_failed_not_verified';r[:error]=e.message;failures<<e.message
       ensure
        created.compact.each{|g|g.erase! if g.valid?}
       end
      end
     ensure;m.abort_operation;end
    end
    out={status:'candidates_require_design_intent_review',instances_scanned:all.length,candidate_count:candidates.length,exact_tested:tested,candidates:candidates,kernel_failures:failures,limits:['World transforms accumulated through nested groups/components.','AABB is broad phase only; curved objects may produce false positives.','Native solid intersection is attempted only for closed manifold leaves, with an explicit cap.','Touching or <= tolerance overlaps are omitted; thin glass, partial coplanar z-fighting, non-solids, mirrored-degenerate geometry require separate face and visual review.','Confirmed volume overlap can be a legitimate join; no automatic shrink/hide/delete.']};write_json(p['output'],out) if p['output'];out
   end
   def self.clashes_start(p)
    raise 'Another quality operation/save is pending' if @busy || @in_job
    base=clashes(p.merge('exact'=>false));pairs=base[:candidates].select{|r|r[:solid_a] && r[:solid_b]}.first(p.fetch('max_exact',30).to_i)
    dir=File.expand_path(p.fetch('output_dir'));FileUtils.mkdir_p(dir);id=SecureRandom.hex(6);file=dir+'/clashes_'+id+'.json'
    job={id:id,kind:'clashes',status:'running',report:file,started_at:Time.now.to_s,model_path:Sketchup.active_model.path,instances_scanned:base[:instances_scanned],candidate_count:base[:candidate_count],planned_exact:pairs.length,attempted:0,candidates:base[:candidates],limits:base[:limits]}
    @jobs[id]=job;@busy={id:id,kind:'clashes'};write_json(file,job);queue=pairs.dup
    runner=nil
    runner=lambda do
     begin
      @in_clash_tick=true
      p.fetch('batch_size',2).to_i.clamp(1,4).times do
       r=queue.shift;break unless r
       rr=clashes({'paths'=>[r[:a],r[:b]],'only_pairs'=>[[r[:a],r[:b]]],'exact'=>true,'max_exact'=>1,'tolerance_mm'=>p.fetch('tolerance_mm',1)})
       got=rr[:candidates].first;r.merge!(got) if got;job[:attempted]+=1
      end
      @in_clash_tick=false
      job[:confirmed_overlaps]=job[:candidates].count{|r|r[:narrow]=='confirmed_solid_overlap_review_intent'}
      job[:unverified_kernel_results]=job[:candidates].count{|r|r[:narrow].include?('unverified') || r[:narrow].include?('failed')}
      if queue.empty?
       job[:status]='checked_candidates_require_design_intent_review';job[:finished_at]=Time.now.to_s;@busy=nil
      else
       UI.start_timer(0.05,false){runner.call}
      end
      write_json(file,job)
     rescue Exception=>ex
      @in_clash_tick=false;@busy=nil;job[:status]='check_failed_unverified';job[:error]={class:ex.class.name,message:ex.message,backtrace:ex.backtrace&.first(15)};write_json(file,job)
     end
    end
    UI.start_timer(0.1,false){runner.call};job
   end
   def self.mesh_slab(p)
    timer=Process.clock_gettime(Process::CLOCK_MONOTONIC);timings={}
    stamp=lambda do |label|
     now=Process.clock_gettime(Process::CLOCK_MONOTONIC);timings[label]=now-timer;timer=now
    end
    data=p['mesh'] || JSON.parse(File.read(p.fetch('mesh_file')));es,parent_world=model_context(p)
    id=p['ai_id'];digest=spec_digest(p.merge('mesh'=>data).reject{|k,v|k=='mesh_file'});old=existing_in(id,es)
    if old && old.get_attribute('su_mcp_bridge','build_spec_sha256')==digest
     stamp.call('lookup');return {status:'unchanged',path:(p['parent_path'] || (Sketchup.active_model.active_path || []).map(&:persistent_id))+[old.persistent_id],manifold:old.manifold?,rebuilt:false,timings_seconds:timings}
    end
    job_progress('mesh_validate',nil,nil,{'ai_id'=>id})
    top=data.fetch('vertices_mm').map{|v|numbers(v,3,'mesh vertex')};th=Float(p.fetch('thickness_mm',180));raise 'Positive finite thickness required' unless th.finite? && th>0
    bottom=data['bottom_vertices_mm'] ? data['bottom_vertices_mm'].map{|v|numbers(v,3,'bottom vertex')} : top.map{|v|[v[0],v[1],v[2]-th]}
    raise 'Bottom vertex count must match top' unless bottom.size==top.size
    tt=data.fetch('triangles');raise 'Mesh triangle limit200000; split into editable patches' unless tt.is_a?(Array) && !tt.empty? && tt.size<=200000
    edges={};seen={};triangles=tt.map do |ids|
     raise 'Invalid triangle indices' unless ids.is_a?(Array) && ids.size==3 && ids.uniq.size==3 && ids.all?{|i|i.is_a?(Integer) && i>=0 && i<top.size}
     sig=ids.sort;raise 'Duplicate heightfield triangle' if seen[sig];seen[sig]=true
     a,b,c=ids.map{|i|top[i]};aa,bb,cc=ids.map{|i|bottom[i]};det=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);db=(bb[0]-aa[0])*(cc[1]-aa[1])-(bb[1]-aa[1])*(cc[0]-aa[0])
     raise 'Folded or degenerate heightfield triangle' unless det.abs>0.01 && det*db>0
     raise 'Bottom crosses corresponding top vertex' unless ids.all?{|i|bottom[i][2]<top[i][2]-0.01}
     ids=ids.reverse if det<0
     ids.each_with_index{|a,i|b=ids[(i+1)%3];(edges[[a,b].sort]||=[])<<[a,b]};ids
    end
    raise 'Non-manifold triangle topology' if edges.values.any?{|v|v.size>2 || (v.size==2 && v[0]!=v[1].reverse)}
    space=p.fetch('coordinate_space','local');raise 'coordinate_space must be local or world' unless ['local','world'].include?(space)
    inverse=parent_world.inverse if space=='world'
    convert=lambda{|v|pt=Geom::Point3d.new(*v.map{|x|x.mm});inverse ? inverse*pt : pt}
    verts=top.map(&convert);bottoms=bottom.map(&convert)
    flip=space=='world' && parent_world.xaxis.cross(parent_world.yaxis).dot(parent_world.zaxis)<0
    boundary=edges.values.select{|v|v.size==1}.map(&:first)
    stamp.call('prepare_validate')
    job_progress('mesh_build_polygon_buffer',0,triangles.size,{'ai_id'=>id})
    pm=Geom::PolygonMesh.new(verts.size*2,triangles.size*2+boundary.size*2)
    vi=verts.map{|v|pm.add_point(v)};bi=bottoms.map{|v|pm.add_point(v)}
    polygon=lambda{|ids|pm.add_polygon(flip ? ids.reverse : ids)}
    triangles.each{|ids|polygon.call(ids.map{|i|vi[i]});polygon.call(ids.reverse.map{|i|bi[i]})}
    boundary.each{|i,j|polygon.call([vi[j],vi[i],bi[i]]);polygon.call([vi[j],bi[i],bi[j]])}
    stamp.call('polygon_buffer')
    with_operation('SAIE upsert editable mesh slab') do
     group=es.add_group;group.name=p.fetch('name','Editable mesh slab');ee=group.entities
     job_progress('mesh_native_fill',triangles.size,triangles.size,{'ai_id'=>id,'native_call_may_block'=>true})
     raise 'Native bulk mesh population failed' unless ee.fill_from_mesh(pm,true,0)
     stamp.call('native_fill')
     raise 'Native result is not a closed solid' unless group.manifold?
     ee.grep(Sketchup::Edge).each{|e|if e.faces.size==2 && e.faces[0].normal.angle_between(e.faces[1].normal)<14.degrees;e.soft=true;e.smooth=true;end}
     stamp.call('closedness_and_smoothing')
     old.erase! if old;built(group,id,digest,p) if id
     job_progress('mesh_complete',triangles.size,triangles.size,{'ai_id'=>id})
     {status:old ? 'updated' : 'created',path:(p['parent_path'] || (Sketchup.active_model.active_path || []).map(&:persistent_id))+[group.persistent_id],faces:ee.grep(Sketchup::Face).size,manifold:true,thickness_mm:th,rebuilt:true,backend:'native_polygon_mesh_bulk',timings_seconds:timings}
    end
   end
   def self.boolean_solid(p)
    with_operation('SAIE verified solid Boolean') do
     ap=p.fetch('target_path');bp=p.fetch('tool_path')
     raise 'Solid Boolean requires a common parent instance path' unless ap[0...-1]==bp[0...-1] && ap!=bp
     a,at=find_path(ap);b,bt=find_path(bp)
     raise 'Both Boolean inputs must be manifold solids' unless a.manifold? && b.manifold?
     parent=a.parent;es=parent.respond_to?(:entities) ? parent.entities : parent
     attempts=[];result=nil
     [1.0,10.0,100.0].each do |factor|
      copies=[];candidate=nil
      begin
       [a,b].each do |ent|
        g=es.add_group;copies<<g;inst=g.entities.add_instance(ent.definition,ent.transformation);inst.explode;g.transform!(Geom::Transformation.scaling(factor))
       end
       candidate=p.fetch('operation','subtract')=='union' ? copies[0].union(copies[1]) : copies[1].subtract(copies[0])
       if candidate && candidate.valid? && candidate.manifold?
        candidate.transform!(Geom::Transformation.scaling(1.0/factor));result=candidate;attempts<<{factor:factor,status:'closed_solid'};break
       end
       attempts<<{factor:factor,status:'kernel_failed_or_nonclosed'}
      rescue=>ex
       attempts<<{factor:factor,status:'kernel_exception',error:ex.message}
      ensure
       copies.each{|g|g.erase! if g.valid? && g!=candidate}
       candidate.erase! if candidate && candidate.valid? && candidate!=result
      end
     end
     raise 'Native solid Boolean failed; original inputs preserved: '+attempts.inspect unless result
     name=p['name'] || a.name;[a,b].each{|ent|ent.erase! if ent.valid?};result.name=name
     {status:'closed_solid_result_requires_visual_review',result_path:ap[0...-1]+[result.persistent_id],attempts:attempts,manifold:result.manifold?}
    end
   end
   def self.bundle(p)
    dir=File.expand_path(p.fetch('output_dir'));FileUtils.mkdir_p(dir)
    inv=inventory({'output'=>dir+'/model_inventory.json'})
    mat=material_audit({'duplicates'=>true,'output'=>dir+'/material_faces.json'})
    cap=capture({'path'=>dir+'/actual_view.png'})
    result={status:'evidence_exported_not_automatic_design_approval',inventory:dir+'/model_inventory.json',material_audit:dir+'/material_faces.json',screenshot:cap,instances:inv[:instances].length}
    if p['clashes']
     clash=clashes({'exact'=>p.fetch('exact',false),'paths'=>p['paths'],'max_exact'=>p.fetch('max_exact',20),'output'=>dir+'/clashes.json'}.reject{|k,v|v.nil?})
     result[:collision_candidates]=clash[:candidate_count];result[:exact_tested]=clash[:exact_tested]
    end
    write_json(dir+'/bundle.json',result);result
   end
   @jobs||={}
   # File checkpoints remain readable by the client while SketchUp's Ruby thread
   # is inside a native call. They are stage progress, not a fictitious heartbeat.
   def self.job_progress(stage,completed=nil,total=nil,detail={})
    return unless @in_job && @active_job
    j=@active_job;now=Process.clock_gettime(Process::CLOCK_MONOTONIC)
    j[:progress]={stage:stage,completed:completed,total:total,detail:detail,updated_at:Time.now.iso8601(3),elapsed_seconds:now-@job_clock}
    (j[:checkpoints]||=[])<<j[:progress];j[:checkpoints]=j[:checkpoints].last(64)
    write_json(j[:report],j)
   end
   def self.job_start(p)
    code=p['code'] || File.binread(p.fetch('ruby_file')).force_encoding('UTF-8');sha=Digest::SHA256.hexdigest(code);m=Sketchup.active_model;key=p['request_key']
    if p['expected_model_path'];raise 'Active model path changed' unless File.expand_path(m.path).casecmp(File.expand_path(p['expected_model_path']))==0;end
    deps=(p['dependencies'] || []).map{|f|{path:File.expand_path(f),sha256:Digest::SHA256.file(f).hexdigest}}
    fingerprint=spec_digest({'code_sha256'=>sha,'dependencies'=>deps})
    # Compile source/dependencies before queueing any model mutation.
    begin
     RubyVM::InstructionSequence.compile(code,p['ruby_file'] || '(SAIE quality job)')
     deps.each{|d|RubyVM::InstructionSequence.compile(File.read(d[:path],encoding:'UTF-8'),d[:path])}
    rescue SyntaxError=>ex
     raise ArgumentError, 'Ruby preflight failed before mutation: '+ex.message
    end
    @requests||={};request_scope=[m.object_id,key]
    if key
     prior=@requests[request_scope] || (JSON.parse(m.get_attribute('WishSuiteBridgeJobs',key)) rescue nil)
     if prior
      prior=prior.transform_keys(&:to_sym)
      matches=prior[:fingerprint] ? prior[:fingerprint]==fingerprint : (deps.empty? && prior[:code_sha256]==sha)
      raise 'request_key conflict: source/dependencies changed; inspect previous status and use a new key' unless matches
      return job_status({'id'=>prior[:id],'report'=>prior[:report]}).merge(replayed:true)
     end
    end
    @failed_specs||={};failed_key=[m.object_id,fingerprint]
    raise 'Identical failed source/dependencies: inspect previous error and change inputs or explicitly request retry_failed' if @failed_specs[failed_key] && !p['retry_failed']
    raise 'A quality job or save is already pending; inspect its status before another mutation' if @busy || @in_job
    dir=File.expand_path(p.fetch('output_dir'));FileUtils.mkdir_p(dir);id=SecureRandom.hex(6);file=dir+'/job_'+id+'.json'
    queued=Process.clock_gettime(Process::CLOCK_MONOTONIC)
    job={id:id,status:'queued',report:file,code_sha256:sha,fingerprint:fingerprint,dependencies:deps,rollback_scope:'SketchUp operation only; filesystem diagnostics remain',queued_at:Time.now.iso8601(3),started_at:Time.now.to_s};@jobs[id]=job;@requests[request_scope]=job if key;@busy={id:id,kind:'job'};write_json(file,job)
    UI.start_timer(0.3,false) do
     started=Process.clock_gettime(Process::CLOCK_MONOTONIC);operation_started=false
     begin
      raise 'Active model changed while job was queued' unless Sketchup.active_model==m
      raise 'Source changed after submission' if p['ruby_file'] && !p['code'] && Digest::SHA256.file(p['ruby_file']).hexdigest!=sha
      deps.each{|d|raise 'Dependency changed after submission: '+d[:path] unless Digest::SHA256.file(d[:path]).hexdigest==d[:sha256]}
      @job_clock=started;@active_job=job;@in_job=true;job[:status]='running';job[:execution_started_at]=Time.now.iso8601(3);job[:queue_seconds]=started-queued
      m.start_operation('SAIE verified job '+id,true);operation_started=true
      job_progress('execute_ruby')
      result=eval(code,TOPLEVEL_BINDING,p['ruby_file'] || '(SAIE quality job)')
      m.set_attribute('WishSuiteBridgeJobs',key,JSON.generate({id:id,report:file,code_sha256:sha,fingerprint:fingerprint})) if key
      m.commit_operation;job[:status]='executed_requires_validation';job[:result]=result.inspect;job[:model_path]=m.path
     rescue Exception=>ex
      m.abort_operation if operation_started;WishSuiteBridge::AI_ID.reset;job[:status]=operation_started ? 'failed_rolled_back' : 'failed_before_mutation';@failed_specs[failed_key]=job[:id];job[:error]={class:ex.class.name,message:ex.message,backtrace:ex.backtrace&.first(30)}
     ensure
      @in_job=false;@active_job=nil;@busy=nil;job[:execution_seconds]=Process.clock_gettime(Process::CLOCK_MONOTONIC)-started;job[:total_seconds]=Process.clock_gettime(Process::CLOCK_MONOTONIC)-queued;job[:finished_at]=Time.now.iso8601(3);write_json(file,job)
     end
    end
    job
   end
   def self.save_start(p)
    raise 'A quality operation or native save is pending' if @busy || @in_job
    path=File.expand_path(p.fetch('path'));raise 'Expected .skp output' unless File.extname(path).downcase=='.skp'
    raise 'Output exists; use a new revision or explicit overwrite flag' if File.exist?(path) && !p['overwrite']
    dir=p.fetch('output_dir',File.dirname(path));FileUtils.mkdir_p(dir);FileUtils.mkdir_p(File.dirname(path))
    id=SecureRandom.hex(6);file=dir+'/save_'+id+'.json';m=Sketchup.active_model
    job={id:id,kind:'save',status:'waiting_for_native_save',output:path,report:file,started_at:Time.now.to_s};@jobs[id]=job;@busy={id:id,kind:'save'};write_json(file,job)
    UI.start_timer(0.3,false) do
     begin
      ok=m.save(path)
      raise 'Native save did not produce a non-empty SKP' unless ok && File.file?(path) && File.size(path)>0
      job[:status]='saved_requires_reopen_validation';job[:model_path]=m.path;job[:bytes]=File.size(path);job[:sha256]=Digest::SHA256.file(path).hexdigest
     rescue Exception=>ex
      job[:status]='save_failed';job[:error]={class:ex.class.name,message:ex.message,backtrace:ex.backtrace&.first(15)}
     ensure
      @busy=nil;job[:finished_at]=Time.now.to_s;write_json(file,job)
     end
    end
    job
   end
   def self.job_status(p)
    id=p.fetch('id');return @jobs[id] if @jobs[id]
    if p['report'] && File.file?(p['report'])
     r=JSON.parse(File.read(p['report']));r['status']='previous_session_incomplete_unverified' if ['running','queued','waiting_for_native_save'].include?(r['status']);r
    else;{status:'unknown_job'};end
   end
  end
 end
end
