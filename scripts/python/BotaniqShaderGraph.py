"""Translate extracted botaniq signal graphs using standard MaterialX operations.

The compiler preserves connected source controls and closure mixtures. Noise and
BSDF implementations remain renderer dependent; unsupported operations fail with
their source node name rather than disappearing from the material.
"""
import math

KINDS = {'RGBA': 'color3', 'VECTOR': 'vector3', 'VALUE': 'float',
         'INT': 'integer', 'BOOLEAN': 'boolean', 'SHADER': 'surfaceshader'}


class Compiler:
    def __init__(self, graph, source, options):
        self.g, self.source, self.options = graph, source, options
        self.cache = {}

    def val(self, value, kind='float'):
        return self.g.literal(value, kind)

    def node(self, op, kind='float', **inputs):
        return self.g.add(op, kind, op, **inputs)

    def cast(self, value, kind):
        if value['type'] == kind:
            return value
        if 'value' in value:
            v = value['value']
            if kind == 'float' and isinstance(v, (list, tuple)):
                v = sum(a*b for a,b in zip(v, (.2126,.7152,.0722)))
            return self.val(v, kind)
        if kind == 'float' and value['type'] in {'color3','vector3'}:
            if value['type'] == 'color3':
                value = self.node('luminance', 'color3', **{'in': value})
            return self.node('extract', index=self.val(0,'integer'), **{'in':value})
        return self.node('convert', kind, **{'in':value})

    def expr(self, signal, kind=None):
        natural = KINDS.get(signal.get('kind'), 'float')
        if 'value' in signal:
            result = self.val(signal['value'], natural)
        else:
            index = signal['ref']
            if index not in self.cache:
                record = self.source['nodes'][index]
                try:
                    self.cache[index] = self.compile(record)
                except Exception as exc:
                    raise ValueError('{} / {}: {}'.format(record['type'], record['name'], exc)) from exc
            result = self.cache[index]
        return self.cast(result, kind) if kind else result

    def binary(self, op, a, b, kind='float'):
        return self.node(op, kind, in1=self.cast(a,kind), in2=self.cast(b,kind))

    def clamp(self, a):
        return self.node('clamp', a['type'], **{'in':a,'low':self.val(0,a['type']),'high':self.val(1,a['type'])})

    def mix(self, a, b, fac):
        return self.node('mix', a['type'], bg=a,fg=self.cast(b,a['type']),mix=self.cast(fac,'float'))

    def hsv(self, color, hue, sat, value):
        amount=self.node('combine3','vector3',in1=self.binary('subtract',hue,self.val(.5)),in2=sat,in3=value)
        return self.node('hsvadjust','color3', **{'in':color,'amount':amount})

    def ramp(self, factor, elements, interpolation='LINEAR', kind='color3'):
        # Explicit pieces preserve every authored stop (including seasonal ramps).
        result=self.val(elements[0][1],kind)
        for (left,a),(right,b) in zip(elements,elements[1:]):
            if right == left:
                fac=self.node('ifgreatereq',value1=factor,value2=self.val(right),in1=self.val(1),in2=self.val(0))
            elif interpolation=='CONSTANT':
                fac=self.node('ifgreatereq',value1=factor,value2=self.val(right),in1=self.val(1),in2=self.val(0))
            else:
                fac=self.clamp(self.binary('divide',self.binary('subtract',factor,self.val(left)),self.val(right-left)))
                if interpolation=='EASE':
                    fac=self.binary('multiply',self.binary('multiply',fac,fac),self.binary('subtract',self.val(3),self.binary('multiply',self.val(2),fac)))
                elif interpolation not in {'LINEAR','EASE'}:
                    raise ValueError('Unsupported ramp interpolation ' + interpolation)
            result=self.mix(result,self.val(b,kind),fac)
        return result

    def compile(self, r):
        g=self.g
        kind=KINDS.get(r['kind'],'float')
        entries=r.get('inputs',[])
        def get(name, default=0, k='float', occurrence=0):
            found=[s['signal'] for s in entries if s['name']==name]
            return self.expr(found[occurrence],k) if len(found)>occurrence else self.val(default,k or 'float')
        def at(i,k=None):
            return self.expr(entries[i]['signal'],k)
        def normal(name='Normal'):
            n=get(name,[0,0,0],'vector3')
            return self.node('normal','vector3',space=self.val('world','string')) if n.get('value')==[0,0,0] else n
        t=r['type']
        if t=='Control':
            name=r['name']
            aliases={'bq_season_offset':'season','bq_brightness':'source_brightness',
                     'bq_random_per_leaf':'random_per_leaf','bq_random_per_branch':'random_per_branch'}
            value=self.options.get('controls',{}).get(name,r['value'])
            if aliases.get(name) in self.options:
                value=self.options[aliases[name]]
            elif r.get('group')=='instance' and name not in self.options.get('controls',{}):
                return g.add('geompropvalue',kind,name,geomprop=self.val(name,'string'),default=self.val(value,kind))
            return g.add('constant',kind,name,value=self.val(value,kind))
        if t=='ShaderNodeTexImage':
            if not r.get('image'):
                raise ValueError('Image node has no image')
            if r.get('projection','FLAT') == 'BOX':
                image_kind='color4' if r['output']=='Alpha' else 'color3'
                position=get('Vector',[0,0,0],'vector3') if r['vector_linked'] else self.node('geompropvalue','vector3',geomprop=self.val('bq_generated','string'))
                image=g.add('triplanarprojection',image_kind,r['name'],
                            filex=self.val(r['image'],'filename'), filey=self.val(r['image'],'filename'),
                            filez=self.val(r['image'],'filename'),position=position,
                            blend=self.val(r.get('projection_blend',0)))
                g.nodes[-1]['colorspace']='srgb_texture' if r.get('colorspace')=='sRGB' and image_kind=='color3' else 'raw'
                return self.node('extract',index=self.val(3,'integer'),**{'in':image}) if image_kind=='color4' else image
            if r.get('projection','FLAT') != 'FLAT':
                raise ValueError('Image projection ' + r['projection'])
            uv=get('Vector',[0,0,0],'vector3') if r['vector_linked'] else self.node('texcoord','vector2',index=self.val(0,'integer'))
            uv=self.cast(uv,'vector2')
            image_kind='color4' if r['output']=='Alpha' else 'color3'
            image=g.add('image',image_kind,r['name'],file=self.val(r['image'],'filename'),texcoord=uv,
                        uaddressmode=self.val({'REPEAT':'periodic','EXTEND':'clamp','CLIP':'constant','MIRROR':'mirror'}[r['extension']],'string'),
                        vaddressmode=self.val({'REPEAT':'periodic','EXTEND':'clamp','CLIP':'constant','MIRROR':'mirror'}[r['extension']],'string'))
            g.nodes[-1]['colorspace']='srgb_texture' if r.get('colorspace')=='sRGB' and image_kind=='color3' else 'raw'
            return self.node('extract',index=self.val(3,'integer'),**{'in':image}) if image_kind=='color4' else image
        if t=='ShaderNodeUVMap':
            name=r.get('uv_map') or 'UVMap'
            if name in self.options.get('primary_uvs',['UVMap','UV_Map','uv','st']):
                return self.cast(self.node('texcoord','vector2',index=self.val(0,'integer')),'vector3')
            return self.cast(self.node('geompropvalue','vector2',geomprop=self.val(name,'string')),'vector3')
        if t=='ShaderNodeTexCoord':
            output=r['output']
            if output=='UV':return self.cast(self.node('texcoord','vector2',index=self.val(0,'integer')),'vector3')
            if output=='Generated':return self.node('geompropvalue','vector3',geomprop=self.val('bq_generated','string'))
            if output=='Object':return self.node('position','vector3',space=self.val('object','string'))
            if output=='Normal':return self.node('normal','vector3',space=self.val('object','string'))
        if t=='ShaderNodeNewGeometry':
            if r['output']=='Position':return self.node('position','vector3',space=self.val('world','string'))
            if r['output'] in {'Normal','True Normal'}:return normal()
            if r['output']=='Pointiness':return self.node('geompropvalue',geomprop=self.val('bq_pointiness','string'),default=self.val(.5))
            if r['output']=='Backfacing':return self.node('backfacing')
            if r['output']=='Incoming':return self.node('viewdirection','vector3',space=self.val('world','string'))
        if t=='ShaderNodeObjectInfo':
            if r['output']=='Random':return self.node('geompropvalue',geomprop=self.val('bq_object_random','string'),default=self.val(.5))
            if r['output']=='Location':return self.node('geompropvalue','vector3',geomprop=self.val('bq_object_location','string'))
            if r['output']=='Color':return self.node('geompropvalue','color3',geomprop=self.val('bq_object_color','string'),default=self.val([1,1,1],'color3'))
        if t=='ShaderNodeAttribute':
            return self.node('geompropvalue',kind,geomprop=self.val(r['attribute_name'],'string'))
        if t=='ShaderNodeMapping':
            v=get('Vector',[0,0,0],'vector3'); scale=get('Scale',[1,1,1],'vector3')
            if r['vector_type']=='NORMAL':
                v=self.binary('divide',v,scale,'vector3')
            else:v=self.binary('multiply',v,scale,'vector3')
            rotation=get('Rotation',[0,0,0],'vector3')
            for i,axis in enumerate(([1,0,0],[0,1,0],[0,0,1])):
                angle=self.binary('multiply',self.node('extract',index=self.val(i,'integer'),**{'in':rotation}),self.val(180/math.pi))
                v=self.node('rotate3d','vector3',**{'in':v,'amount':angle,'axis':self.val(axis,'vector3')})
            if r['vector_type']=='POINT':v=self.binary('add',v,get('Location',[0,0,0],'vector3'),'vector3')
            if r['vector_type']=='NORMAL':v=self.node('normalize','vector3',**{'in':v})
            if r['vector_type']=='TEXTURE':raise ValueError('Inverse texture mapping is not yet supported')
            return v
        if t in {'ShaderNodeSeparateXYZ','ShaderNodeSeparateColor'}:
            v=at(0,'vector3' if t.endswith('XYZ') else 'color3')
            if r.get('mode')=='HSV':v=self.node('rgbtohsv','color3',**{'in':v})
            index={'X':0,'Y':1,'Z':2,'Red':0,'Green':1,'Blue':2,'Hue':0,'Saturation':1,'Value':2}[r['output']]
            return self.node('extract',index=self.val(index,'integer'),**{'in':v})
        if t in {'ShaderNodeCombineXYZ','ShaderNodeCombineColor'}:
            v=self.node('combine3',kind,in1=at(0,'float'),in2=at(1,'float'),in3=at(2,'float'))
            return self.node('hsvtorgb','color3',**{'in':v}) if r.get('mode')=='HSV' else v
        if t=='ShaderNodeMath':
            op=r['operation']; a=at(0,'float'); b=at(1,'float') if len(entries)>1 else self.val(0)
            binary={'ADD':'add','SUBTRACT':'subtract','MULTIPLY':'multiply','DIVIDE':'divide','POWER':'power','MINIMUM':'min','MAXIMUM':'max','MODULO':'modulo','ARCTAN2':'atan2'}
            unary={'SINE':'sin','COSINE':'cos','TANGENT':'tan','ABSOLUTE':'absval','FLOOR':'floor','CEIL':'ceil','ROUND':'round','SQRT':'sqrt','SIGN':'sign','ARCSINE':'asin','ARCCOSINE':'acos','ARCTANGENT':'atan','EXPONENT':'exp'}
            if op in binary:v=self.binary(binary[op],a,b)
            elif op in unary:v=self.node(unary[op],**{'in':a})
            elif op=='MULTIPLY_ADD':v=self.binary('add',self.binary('multiply',a,b),at(2,'float'))
            elif op=='LESS_THAN':v=self.node('ifgreater',value1=b,value2=a,in1=self.val(1),in2=self.val(0))
            elif op=='GREATER_THAN':v=self.node('ifgreater',value1=a,value2=b,in1=self.val(1),in2=self.val(0))
            elif op=='FRACT':v=self.binary('subtract',a,self.node('floor',**{'in':a}))
            else:raise ValueError('Math operation '+op)
            return self.clamp(v) if r.get('use_clamp') else v
        if t=='ShaderNodeVectorMath':
            op=r['operation'];a=at(0,'vector3')
            if op in {'ADD','SUBTRACT','MULTIPLY','DIVIDE'}:return self.binary(op.lower(),a,at(1,'vector3'),'vector3')
            if op=='NORMALIZE':return self.node('normalize','vector3',**{'in':a})
            if op=='LENGTH':return self.node('magnitude',**{'in':a})
            if op=='DOT_PRODUCT':return self.node('dotproduct',in1=a,in2=at(1,'vector3'))
            if op=='SCALE':return self.binary('multiply',a,get('Scale',1),'vector3')
            raise ValueError('Vector operation '+op)
        if t=='ShaderNodeMapRange':
            x=get('Value');lo=get('From Min');hi=get('From Max',1)
            f=self.binary('divide',self.binary('subtract',x,lo),self.binary('subtract',hi,lo))
            if r.get('clamp') or r.get('interpolation_type') in {'SMOOTHSTEP','SMOOTHERSTEP'}:f=self.clamp(f)
            if r.get('interpolation_type')=='SMOOTHSTEP':f=self.binary('multiply',self.binary('multiply',f,f),self.binary('subtract',self.val(3),self.binary('multiply',self.val(2),f)))
            elif r.get('interpolation_type','LINEAR')!='LINEAR':raise ValueError('Map Range interpolation '+r['interpolation_type'])
            return self.binary('add',get('To Min'),self.binary('multiply',f,self.binary('subtract',get('To Max',1),get('To Min'))))
        if t=='ShaderNodeValToRGB':
            if r['ramp']['color_mode']!='RGB':raise ValueError('Non-RGB ramp interpolation')
            stops=r['ramp']['elements']
            if r['output']=='Alpha':stops=[(p,c[3]) for p,c in stops]
            return self.ramp(get('Fac'),stops,r['ramp']['interpolation'],kind)
        if t=='ShaderNodeHueSaturation':
            c=get('Color',0,'color3')
            return self.mix(c,self.hsv(c,get('Hue',.5),get('Saturation',1),get('Value',1)),get('Fac',1))
        if t=='ShaderNodeRGBToBW':return self.cast(at(0,'color3'),'float')
        if t=='ShaderNodeBrightContrast':
            a=self.binary('add',self.val(1),self.binary('divide',get('Contrast'),self.val(100)))
            b=self.binary('subtract',self.binary('divide',get('Bright'),self.val(100)),self.binary('divide',get('Contrast'),self.val(200)))
            return self.binary('max',self.binary('add',self.binary('multiply',get('Color',0,'color3'),a,'color3'),b,'color3'),self.val(0,'color3'),'color3')
        if t=='ShaderNodeInvert':return self.mix(get('Color',0,'color3'),self.binary('subtract',self.val(1,'color3'),get('Color',0,'color3'),'color3'),get('Fac',1))
        if t in {'ShaderNodeMix','ShaderNodeMixRGB'}:
            a=get('A',0,kind) if t=='ShaderNodeMix' else at(1,kind)
            b=get('B',0,kind) if t=='ShaderNodeMix' else at(2,kind)
            f=get('Factor',.5) if t=='ShaderNodeMix' else at(0,'float')
            if r.get('clamp_factor'):f=self.clamp(f)
            mode=r.get('blend_type','MIX')
            if mode=='MIX':v=b
            elif mode=='DIFFERENCE':v=self.node('absval',kind,**{'in':self.binary('subtract',a,b,kind)})
            elif mode in {'MULTIPLY','ADD','SUBTRACT','DIVIDE'}:v=self.binary(mode.lower(),a,b,kind)
            elif mode=='SCREEN':v=self.binary('subtract',self.val(1,kind),self.binary('multiply',self.binary('subtract',self.val(1,kind),a,kind),self.binary('subtract',self.val(1,kind),b,kind),kind),kind)
            elif mode in {'OVERLAY','SOFT_LIGHT','DARKEN','LIGHTEN','COLOR','HUE','SATURATION','VALUE'}:
                operations={'OVERLAY':'overlay','SOFT_LIGHT':'softlight','DARKEN':'min','LIGHTEN':'max'}
                if mode in {'DARKEN','LIGHTEN'}:v=self.binary(operations[mode],a,b,kind)
                elif mode in {'OVERLAY','SOFT_LIGHT'}:v=self.node(operations[mode],kind,fg=b,bg=a,mix=self.val(1))
                else:
                    ah=self.node('rgbtohsv','color3',**{'in':a});bh=self.node('rgbtohsv','color3',**{'in':b})
                    channels=[]
                    for i in range(3):
                        pick=bh if mode=='COLOR' and i<2 or mode=='HUE' and i==0 or mode=='SATURATION' and i==1 or mode=='VALUE' and i==2 else ah
                        channels.append(self.node('extract',index=self.val(i,'integer'),**{'in':pick}))
                    v=self.node('hsvtorgb','color3',**{'in':self.node('combine3','color3',in1=channels[0],in2=channels[1],in3=channels[2])})
            else:raise ValueError('Blend mode '+mode)
            v=self.mix(a,v,f)
            return self.clamp(v) if r.get('clamp_result') or r.get('use_clamp') else v
        if t=='ShaderNodeNormalMap':
            if r.get('space')!='TANGENT':raise ValueError('Normal map space '+r['space'])
            return self.node('normalmap','vector3',**{'in':get('Color',[.5,.5,1],'vector3'),'scale':get('Strength',1)})
        if t=='ShaderNodeBump':
            scale=self.binary('multiply',get('Distance',1),self.val(-1 if r.get('invert') else 1))
            bumped=self.node('bump','vector3',height=get('Height'),scale=scale,normal=normal())
            return self.node('normalize','vector3',**{'in':self.mix(normal(),bumped,self.clamp(get('Strength',1)))})
        if t=='ShaderNodeNormal':
            direction=self.val(r.get('direction',[0,0,1]),'vector3')
            if r['output']=='Dot':return self.node('dotproduct',in1=normal(),in2=direction)
            return direction
        if t=='ShaderNodeTexNoise':
            if r.get('noise_dimensions','3D')!='3D':raise ValueError('Noise dimension '+r['noise_dimensions'])
            if r.get('noise_type','FBM')!='FBM':raise ValueError('Noise type '+r['noise_type'])
            pos=get('Vector',[0,0,0],'vector3')
            if pos.get('value')==[0,0,0]:pos=self.node('geompropvalue','vector3',geomprop=self.val('bq_generated','string'))
            pos=self.binary('multiply',pos,get('Scale',5),'vector3')
            distortion=self.node('noise3d','vector3',position=pos)
            pos=self.binary('add',pos,self.binary('multiply',distortion,get('Distortion'),'vector3'),'vector3')
            detail=self.node('clamp',**{'in':get('Detail',2),'low':self.val(0),'high':self.val(15)})
            whole=self.node('floor',**{'in':detail})
            octaves=self.binary('add',whole,self.val(1))
            fraction=self.binary('subtract',detail,whole)
            roughness=self.clamp(get('Roughness',.5)); lacunarity=get('Lacunarity',2)
            value=self.val(0,kind); weight=self.val(0)
            extra=self.val(0,kind); extra_weight=self.val(0)
            frequency=self.val(1); amplitude=self.val(1)
            # Fixed topology keeps octave counts portable: MaterialX has no float-to-int convert.
            for octave in range(17):
                sample=self.node('noise3d',kind,position=self.binary('multiply',pos,frequency,'vector3'))
                active=self.node('ifgreatereq',value1=whole,value2=self.val(octave),in1=amplitude,in2=self.val(0))
                next_weight=self.node('ifequal',value1=octaves,value2=self.val(octave),in1=amplitude,in2=self.val(0))
                value=self.binary('add',value,self.binary('multiply',sample,active,kind),kind)
                weight=self.binary('add',weight,active)
                extra=self.binary('add',extra,self.binary('multiply',sample,next_weight,kind),kind)
                extra_weight=self.binary('add',extra_weight,next_weight)
                amplitude=self.binary('multiply',amplitude,roughness)
                frequency=self.binary('multiply',frequency,lacunarity)
            extended=self.binary('add',value,extra,kind)
            if r.get('normalize',True):
                value=self.binary('divide',value,weight,kind)
                extended=self.binary('divide',extended,self.binary('add',weight,extra_weight),kind)
                value=self.binary('add',self.binary('multiply',value,self.val(.5),kind),self.val(.5),kind)
                extended=self.binary('add',self.binary('multiply',extended,self.val(.5),kind),self.val(.5),kind)
            return self.mix(value,extended,fraction)
        if t=='ShaderNodeTexGradient':
            v=get('Vector',[0,0,0],'vector3');x=self.node('extract',index=self.val(0,'integer'),**{'in':v})
            if r['gradient_type']=='LINEAR':return self.cast(x,kind)
            if r['gradient_type']=='QUADRATIC':return self.cast(self.binary('multiply',x,x),kind)
            if r['gradient_type'] in {'SPHERICAL','QUADRATIC_SPHERE'}:
                value=self.binary('max',self.binary('subtract',self.val(1),self.node('magnitude',**{'in':v})),self.val(0))
                if r['gradient_type']=='QUADRATIC_SPHERE':value=self.binary('multiply',value,value)
                return self.cast(value,kind)
            raise ValueError('Gradient type '+r['gradient_type'])
        if t=='ShaderNodeBsdfPrincipled':
            values={}
            mapping={'Base Color':('base_color','color3'),'Metallic':('metalness','float'),
                     'Roughness':('specular_roughness','float'),'IOR':('specular_IOR','float'),
                     'Alpha':('opacity','color3'),'Subsurface Weight':('subsurface','float'),
                     'Subsurface Radius':('subsurface_radius','color3'),'Subsurface Scale':('subsurface_scale','float'),
                     'Transmission Weight':('transmission','float'),'Coat Weight':('coat','float'),
                     'Coat Roughness':('coat_roughness','float'),'Coat IOR':('coat_IOR','float'),
                     'Coat Tint':('coat_color','color3'),'Sheen Weight':('sheen','float'),
                     'Sheen Roughness':('sheen_roughness','float'),'Sheen Tint':('sheen_color','color3'),
                     'Emission Color':('emission_color','color3'),'Emission Strength':('emission','float'),
                     'Specular Anisotropic':('specular_anisotropy','float'),
                     'Specular Rotation':('specular_rotation','float')}
            for s in entries:
                if s['name'] in mapping:
                    key,k=mapping[s['name']];values[key]=self.expr(s['signal'],k)
            values['specular']=self.binary('multiply',get('Specular IOR Level',.5),self.val(2))
            values['specular_color']=get('Specular Tint',[1,1,1],'color3')
            values['coat_normal']=normal('Coat Normal')
            values['thin_film_thickness']=get('Thin Film Thickness')
            values['thin_film_IOR']=get('Thin Film IOR',1.33)
            tint=[x*self.options.get('brightness',1) for x in self.options.get('tint',[1,1,1])]
            if tint!=[1,1,1]:
                values['base_color']=self.binary('multiply',values['base_color'],self.val(tint,'color3'),'color3')
            values['normal']=normal()
            return g.add('standard_surface','surfaceshader',r['name'],**values)
        if t=='ShaderNodeBsdfTransparent':
            return self.node('standard_surface','surfaceshader',opacity=self.val(0,'color3'))
        if t in {'ShaderNodeBsdfTranslucent','ShaderNodeBsdfDiffuse'}:
            bsdf=self.node('translucent_bsdf' if t.endswith('Translucent') else 'oren_nayar_diffuse_bsdf','BSDF',weight=self.val(1),color=get('Color',.8,'color3'),normal=normal())
            return self.node('surface','surfaceshader',bsdf=bsdf)
        if t=='ShaderNodeMixShader':return self.mix(get('Shader',0,'surfaceshader'),get('Shader',0,'surfaceshader',1),self.clamp(get('Fac',.5)))
        if t=='ShaderNodeAddShader':return self.binary('add',get('Shader',0,'surfaceshader'),get('Shader',0,'surfaceshader',1),'surfaceshader')
        if t=='ShaderNodeDisplacement':
            return self.node('displacement','displacementshader',displacement=self.binary('subtract',get('Height'),get('Midlevel',.5)),scale=get('Scale',1))
        if t=='ShaderNodeClamp':return self.node('clamp',**{'in':get('Value'),'low':get('Min'),'high':get('Max',1)})
        raise ValueError('Unsupported node/output: '+t+'/'+r.get('output',''))


def compile_material(graph, material, options):
    from BotaniqPortableSurface import lower_graph
    compiler=Compiler(graph,material['source_graph'],options)
    graph.surface=compiler.expr(material['source_graph']['surface'],'surfaceshader')
    if material['source_graph'].get('displacement'):
        graph.displacement=compiler.expr(material['source_graph']['displacement'])
    return lower_graph(graph)
