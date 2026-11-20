# encoding: utf-8
# Small height-field recipe for the existing quality mesh backend; not a new bridge.
module WishSuiteBridge
 module Ops
  module Quality
   def self.control_curve(xs, zs, at)
    ds=xs.each_cons(2).with_index.map{|(a,b),i|(zs[i+1]-zs[i])/(b-a)}
    slopes=[ds.first]+(1...xs.length-1).map do |i|
     a=ds[i-1];b=ds[i];h0=xs[i]-xs[i-1];h1=xs[i+1]-xs[i]
     a*b<=0 ? 0.0 : (3*(h0+h1))/((2*h1+h0)/a+(h1+2*h0)/b)
    end+[ds.last]
    i=(0...xs.length-1).find{|j|at<=xs[j+1]} || xs.length-2
    h=xs[i+1]-xs[i];t=(at-xs[i])/h
    (2*t**3-3*t**2+1)*zs[i]+(t**3-2*t**2+t)*h*slopes[i]+(-2*t**3+3*t**2)*zs[i+1]+(t**3-t**2)*h*slopes[i+1]
   end
   def self.control_mesh(s)
    xs=s.fetch('x_mm').map{|v|Float(v)};ys=s.fetch('y_mm').map{|v|Float(v)}
    raise 'Control stations must be finite, increasing and contain >=2 values' unless [xs,ys].all?{|a|a.length>=2 && a.all?(&:finite?) && a.each_cons(2).all?{|x,y|y>x}}
    z=s.fetch('z_mm');raise 'Control height matrix dimensions mismatch' unless z.length==ys.length && z.all?{|row|row.length==xs.length}
    z=z.map{|row|numbers(row,xs.length,'control heights')}
    nx,ny=s.fetch('segments');raise 'Use integer subdivision counts' unless [nx,ny].all?{|n|n.is_a?(Integer) && n>=2 && n<=500} && nx*ny<=100000
    [ [xs,nx],[ys,ny] ].each{|a,n|raise 'Control stations must coincide with mesh grid; adjust segments' unless a.all?{|x|t=(x-a.first)*n/(a.last-a.first);(t-t.round).abs<1e-7}}
    holes=s.fetch('void_cells',[])
    holes.each{|a|raise 'Void cells require four strictly internal integer bounds' unless a.length==4 && a.all?{|n|n.is_a?(Integer)} && a[0]>0 && a[0]<a[1] && a[1]<nx && a[2]>0 && a[2]<a[3] && a[3]<ny}
    vertices=(0..ny).flat_map do |j|
     y=ys.first+(ys.last-ys.first)*j/ny
     (0..nx).map do |i|
      x=xs.first+(xs.last-xs.first)*i/nx
      [x,y,control_curve(ys,z.map{|row|control_curve(xs,row,x)},y)]
     end
    end
    triangles=[]
    ny.times{|j|nx.times do |i|
     next if holes.any?{|a|i>=a[0] && i<a[1] && j>=a[2] && j<a[3]}
     a=j*(nx+1)+i;triangles<<[a,a+1,a+nx+2]<<[a,a+nx+2,a+nx+1]
    end}
    # Remove unused vertices inside voids; preserve indexed boundary topology.
    used=triangles.flatten.uniq.sort;index={};used.each_with_index{|v,i|index[v]=i}
    {'vertices_mm'=>used.map{|v|vertices[v]},'triangles'=>triangles.map{|t|t.map{|i|index[i]}}}
   end
   def self.mesh_from_controls(p)
    surface=p.fetch('surface');mesh=control_mesh(surface)
    _,parent_world=model_context(p);tr=placement(p,parent_world)
    with_operation('SAIE controlled height field') do
     # Control stations describe a local shape; placement alone may be local/world.
     result=mesh_slab(p.merge('mesh'=>mesh,'coordinate_space'=>'local','control_placement'=>tr.to_a))
     e=WishSuiteBridge::AI_ID.find(p['ai_id']) if p['ai_id']
     if e
      e.transformation=tr
      e.set_attribute('su_mcp_bridge','surface_controls_json',JSON.generate(surface))
      e.set_attribute('su_mcp_bridge','surface_controls_sha256',spec_digest(surface))
     end
     result.merge(control_sha256:spec_digest(surface),control_stations:[surface['x_mm'].length,surface['y_mm'].length])
    end
   end
  end
 end
end
